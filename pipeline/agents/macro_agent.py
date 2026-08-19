"""
Macroeconomic Context Agent
Builds a single daily snapshot of the macro backdrop the rest of the pipeline
reasons against, then stores it in the macro_context table.

Collects, in order:
  1. FRED economic indicators (GDP, CPI, rates, VIX, oil, gold, dollar ...)
  2. Treasury.gov daily yield curve (1M -> 30Y)
  3. Federal Reserve calendar -- FOMC meetings / speeches, next 14 days
  4. Finnhub economic calendar -- this week's macro releases
  5. Finnhub earnings calendar -- next 7 days, filtered to the S&P 1500 universe
  6. Global index ETFs / indices via yfinance
  7. Crypto context (BTC, ETH) via yfinance
  8. Sector SPDR ETFs via yfinance -- drives sector-rotation analysis

It then classifies the market regime (risk_on / risk_off / inflation_concern /
neutral) and generates a 3-paragraph macro narrative (Groq LLM when GROQ_API_KEY
is set, otherwise a deterministic data-driven template).

Every data source degrades gracefully: a failed source logs a warning, leaves
its slice empty, and the run continues. The macro_context insert is likewise
non-fatal -- run supabase/migrations/003_macro_context.sql first to persist it.

Usage:
    python pipeline/agents/macro_agent.py
"""

import csv
import io
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from supabase import create_client

# ── Path setup ─────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (  # noqa: E402
    FINNHUB_API_KEY,
    FRED_API_KEY,
    GROQ_API_KEY,
    SUPABASE_SERVICE_KEY,
    SUPABASE_URL,
    TICKER_UNIVERSE_PATH,
)

# ══════════════════════════════════════════════════════════════════════════════
# Settings
# ══════════════════════════════════════════════════════════════════════════════

HTTP_TIMEOUT = 25
GROQ_MODEL   = "openai/gpt-oss-120b"

# FRED series -> human label.  Daily series resolve week/month-ago exactly;
# monthly/quarterly series fall back to the nearest prior observation.
FRED_SERIES = {
    "GDP":               "Nominal GDP",
    "GDPC1":             "Real GDP",
    "CPIAUCSL":          "CPI",
    "CPILFESL":          "Core CPI",
    "UNRATE":            "Unemployment Rate",
    "PAYEMS":            "Nonfarm Payrolls",
    "DFF":               "Fed Funds Rate",
    "DGS10":             "10Y Treasury Yield",
    "DGS2":              "2Y Treasury Yield",
    "T10Y2Y":            "10Y-2Y Spread",
    "VIXCLS":            "VIX",
    "DCOILWTICO":        "WTI Crude Oil",
    "GOLDPMGBD228NLBM":  "Gold (LBMA PM Fix)",
    "DTWEXBGS":          "Trade-Weighted Dollar Index",
}

# Treasury.gov CSV column -> tenor label we keep.
TREASURY_TENORS = {
    "1 Mo": "1M", "3 Mo": "3M", "6 Mo": "6M", "1 Yr": "1Y",
    "2 Yr": "2Y", "5 Yr": "5Y", "10 Yr": "10Y", "30 Yr": "30Y",
}

GLOBAL_INDICES = {
    "SPY":    "S&P 500 (US)",
    "QQQ":    "Nasdaq 100 (US)",
    "DIA":    "Dow Jones (US)",
    "IWM":    "Russell 2000 (US)",
    "EFA":    "Developed ex-US",
    "EEM":    "Emerging Markets",
    "VEA":    "Developed Markets (Europe-heavy)",
    "VWO":    "Emerging Markets (Vanguard)",
    "^FTSE":  "FTSE 100 (UK)",
    "^GDAXI": "DAX (Germany)",
    "^N225":  "Nikkei 225 (Japan)",
    "^HSI":   "Hang Seng (Hong Kong)",
    "000001.SS": "Shanghai Composite (China)",  # Yahoo symbol for ^SSEC
}

CRYPTO = {"BTC-USD": "Bitcoin", "ETH-USD": "Ethereum"}

SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLV":  "Health Care",
    "XLE":  "Energy",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLI":  "Industrials",
    "XLB":  "Materials",
    "XLU":  "Utilities",
    "XLRE": "Real Estate",
    "XLC":  "Communication Services",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Small helpers
# ══════════════════════════════════════════════════════════════════════════════

def _num(val):
    """Coerce to float, or None for missing / NaN / FRED '.' placeholders."""
    if val is None or val == "" or val == ".":
        return None
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def _pct(latest, ref):
    """Percent change of `latest` vs `ref`; None if not computable."""
    if latest is None or ref is None or ref == 0:
        return None
    return round((latest - ref) / abs(ref) * 100, 3)


# ══════════════════════════════════════════════════════════════════════════════
# 1. FRED economic indicators
# ══════════════════════════════════════════════════════════════════════════════

def _fred_observations(series_id: str) -> list[tuple[datetime, float]]:
    """Return [(date, value), ...] newest-first for a FRED series."""
    resp = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id":  series_id,
            "api_key":    FRED_API_KEY,
            "file_type":  "json",
            "sort_order": "desc",
            "limit":      600,
        },
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    out = []
    for o in resp.json().get("observations", []):
        v = _num(o.get("value"))
        if v is not None:
            out.append((datetime.strptime(o["date"], "%Y-%m-%d"), v))
    return out


def _at_or_before(obs: list[tuple[datetime, float]], target: datetime):
    """First (newest-first) observation dated on or before `target`."""
    for dt, val in obs:
        if dt <= target:
            return val
    return None


def fetch_economic_indicators() -> dict:
    """Pull every FRED series with latest / week-ago / month-ago values."""
    if not FRED_API_KEY:
        log.warning("FRED_API_KEY not set -- skipping economic indicators. "
                    "Get a free key at https://fredaccount.stlouisfed.org/apikeys")
        return {}

    indicators: dict = {}
    for series_id, label in FRED_SERIES.items():
        try:
            obs = _fred_observations(series_id)
            if not obs:
                log.warning("FRED %s (%s): no observations", series_id, label)
                continue
            latest_dt, latest = obs[0]
            week_ago  = _at_or_before(obs, latest_dt - timedelta(days=7))
            month_ago = _at_or_before(obs, latest_dt - timedelta(days=30))
            indicators[series_id] = {
                "label":           label,
                "latest":          latest,
                "latest_date":     latest_dt.date().isoformat(),
                "week_ago":        week_ago,
                "month_ago":       month_ago,
                "pct_change_week":  _pct(latest, week_ago),
                "pct_change_month": _pct(latest, month_ago),
            }
        except Exception as exc:
            log.warning("FRED %s (%s) failed: %s", series_id, label, exc)

    log.info("FRED: pulled %d/%d series", len(indicators), len(FRED_SERIES))
    return indicators


# ══════════════════════════════════════════════════════════════════════════════
# 2. Treasury.gov daily yield curve
# ══════════════════════════════════════════════════════════════════════════════

def _treasury_csv(year: int) -> list[dict]:
    url = ("https://home.treasury.gov/resource-center/data-chart-center/"
           "interest-rates/daily-treasury-rates.csv/{0}/all".format(year))
    resp = requests.get(
        url,
        params={
            "type":                  "daily_treasury_yield_curve",
            "field_tdr_date_value":  year,
            "_format":               "csv",
        },
        headers={"User-Agent": "fortis-pipeline/1.0"},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def fetch_yield_curve() -> dict:
    """Fetch the most recent full daily Treasury par-yield curve."""
    now = datetime.now(timezone.utc)
    for year in (now.year, now.year - 1):   # early-January fallback
        try:
            rows = _treasury_csv(year)
        except Exception as exc:
            log.warning("Treasury %d fetch failed: %s", year, exc)
            continue
        if not rows:
            continue
        # CSV is newest-first; first row is the latest trading day.
        latest = rows[0]
        curve = {"date": latest.get("Date")}
        for col, tenor in TREASURY_TENORS.items():
            curve[tenor] = _num(latest.get(col))
        found = sum(1 for t in TREASURY_TENORS.values() if curve.get(t) is not None)
        log.info("Treasury: yield curve for %s (%d/%d tenors)",
                 curve["date"], found, len(TREASURY_TENORS))
        return curve

    log.warning("Treasury: no yield-curve data available")
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# 3. Federal Reserve calendar (best-effort scrape)
# ══════════════════════════════════════════════════════════════════════════════

_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def fetch_fed_calendar() -> list[dict]:
    """Upcoming FOMC meetings within the next 14 days, scraped from the Fed's
    server-rendered FOMC calendar page. Best-effort: returns [] on any failure."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=14)
    events: list[dict] = []
    try:
        resp = requests.get(
            "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            headers={"User-Agent": "fortis-pipeline/1.0"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        html = resp.text

        # Each meeting renders as a month block followed by a date block, e.g.
        #   ...fomc-meeting__month"><strong>January</strong>...
        #   ...fomc-meeting__date">27-28...
        pattern = re.compile(
            r'fomc-meeting__month[^>]*>\s*(?:<strong>)?\s*([A-Z][a-z]+)'
            r'.*?fomc-meeting__date[^>]*>\s*([0-9]{1,2})(?:-([0-9]{1,2}))?',
            re.S,
        )
        for month_name, d1, d2 in pattern.findall(html):
            month = _MONTHS.get(month_name)
            if not month:
                continue
            end_day = int(d2 or d1)
            # Anchor to the calendar year that places the meeting nearest to now.
            for yr in (now.year, now.year + 1, now.year - 1):
                try:
                    end_dt = datetime(yr, month, end_day, tzinfo=timezone.utc)
                except ValueError:
                    continue
                if now.date() <= end_dt.date() <= horizon.date():
                    events.append({
                        "type":  "FOMC Meeting",
                        "title": f"FOMC Meeting ({month_name} {d1}"
                                 + (f"-{d2}" if d2 else "") + ")",
                        "date":  end_dt.date().isoformat(),
                        "impact": "high",
                    })
                    break
    except Exception as exc:
        log.warning("Fed calendar scrape failed: %s", exc)

    # De-dupe and sort.
    events = list({e["date"]: e for e in events}.values())
    events.sort(key=lambda e: e["date"])
    log.info("Fed calendar: %d FOMC event(s) in next 14 days", len(events))
    return events


# ══════════════════════════════════════════════════════════════════════════════
# 4 & 5. Finnhub economic + earnings calendars
# ══════════════════════════════════════════════════════════════════════════════

def fetch_economic_calendar() -> list[dict]:
    """This week's macro releases from Finnhub's economic-calendar endpoint.
    Note: this endpoint is gated on Finnhub's free tier and may return 403."""
    if not FINNHUB_API_KEY:
        log.warning("FINNHUB_API_KEY not set -- skipping economic calendar")
        return []
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"token": FINNHUB_API_KEY},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 403:
            log.warning("Economic calendar: Finnhub free tier denies "
                        "/calendar/economic (403) -- leaving empty")
            return []
        resp.raise_for_status()
        items = resp.json().get("economicCalendar", []) or []
        now = datetime.now(timezone.utc).date()
        week_end = now + timedelta(days=7)
        out = []
        for it in items:
            day = (it.get("time") or "")[:10]
            try:
                d = datetime.strptime(day, "%Y-%m-%d").date()
            except ValueError:
                continue
            if now <= d <= week_end:
                out.append({
                    "event":    it.get("event"),
                    "time":     it.get("time"),
                    "country":  it.get("country"),
                    "estimate": it.get("estimate"),
                    "prior":    it.get("prev"),
                    "impact":   it.get("impact"),
                })
        out.sort(key=lambda e: e.get("time") or "")
        log.info("Economic calendar: %d release(s) this week", len(out))
        return out
    except Exception as exc:
        log.warning("Economic calendar failed: %s", exc)
        return []


def _load_universe() -> set[str]:
    try:
        return set(
            pd.read_csv(TICKER_UNIVERSE_PATH)["ticker"]
            .dropna().astype(str).str.upper().tolist()
        )
    except Exception as exc:
        log.warning("Could not load ticker universe: %s", exc)
        return set()


def fetch_earnings_calendar() -> list[dict]:
    """Next 7 days of earnings from Finnhub, filtered to the S&P 1500 universe."""
    if not FINNHUB_API_KEY:
        log.warning("FINNHUB_API_KEY not set -- skipping earnings calendar")
        return []
    universe = _load_universe()
    today = datetime.now(timezone.utc).date()
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={
                "from":  today.isoformat(),
                "to":    (today + timedelta(days=7)).isoformat(),
                "token": FINNHUB_API_KEY,
            },
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json().get("earningsCalendar", []) or []
        out = []
        for it in items:
            sym = (it.get("symbol") or "").upper()
            if universe and sym not in universe:
                continue
            out.append({
                "symbol":         sym,
                "date":           it.get("date"),
                "hour":           it.get("hour"),          # bmo | amc | dmh
                "eps_estimate":   it.get("epsEstimate"),
                "revenue_estimate": it.get("revenueEstimate"),
            })
        out.sort(key=lambda e: (e.get("date") or "", e.get("symbol") or ""))
        log.info("Earnings calendar: %d S&P-1500 name(s) reporting in next 7 days",
                 len(out))
        return out
    except Exception as exc:
        log.warning("Earnings calendar failed: %s", exc)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# 6, 7, 8. yfinance -- indices, crypto, sectors
# ══════════════════════════════════════════════════════════════════════════════

def _close_series(tickers: list[str], period: str = "2mo") -> dict[str, pd.Series]:
    """Return {ticker: Close series} for a list of yfinance symbols."""
    try:
        raw = yf.download(
            tickers, period=period, interval="1d",
            auto_adjust=True, progress=False, group_by="ticker",
        )
    except Exception as exc:
        log.warning("yfinance download error: %s", exc)
        return {}
    if raw is None or raw.empty:
        return {}

    out: dict[str, pd.Series] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            try:
                s = raw[t]["Close"].dropna()
                if not s.empty:
                    out[t] = s
            except KeyError:
                pass
    else:                                   # single ticker -> flat columns
        s = raw["Close"].dropna()
        if not s.empty:
            out[tickers[0]] = s
    return out


def _changes(series: pd.Series) -> dict:
    """Latest close plus 1-day, 5-day and ~1-month percent changes."""
    vals = series.tolist()
    last = vals[-1]

    def back(n):
        return vals[-1 - n] if len(vals) > n else None

    return {
        "close":          round(last, 4),
        "day_change_pct": _pct(last, back(1)),
        "five_day_pct":   _pct(last, back(5)),
        "month_pct":      _pct(last, back(21)),
    }


def fetch_global_indices() -> dict:
    series = _close_series(list(GLOBAL_INDICES), period="1mo")
    out = {}
    for tkr, name in GLOBAL_INDICES.items():
        s = series.get(tkr)
        if s is None or len(s) < 2:
            continue
        ch = _changes(s)
        out[tkr] = {"name": name, "close": ch["close"],
                    "day_change_pct": ch["day_change_pct"],
                    "five_day_pct": ch["five_day_pct"]}
    log.info("Global indices: %d/%d resolved", len(out), len(GLOBAL_INDICES))
    return out


def fetch_crypto() -> dict:
    series = _close_series(list(CRYPTO), period="7d")
    out = {}
    for tkr, name in CRYPTO.items():
        s = series.get(tkr)
        if s is None or len(s) < 2:
            continue
        ch = _changes(s)
        out[tkr] = {"name": name, "close": ch["close"],
                    "change_24h_pct": ch["day_change_pct"]}
    log.info("Crypto: %d/%d resolved", len(out), len(CRYPTO))
    return out


def fetch_sector_performance() -> dict:
    series = _close_series(list(SECTOR_ETFS), period="2mo")
    out = {}
    for tkr, name in SECTOR_ETFS.items():
        s = series.get(tkr)
        if s is None or len(s) < 2:
            continue
        ch = _changes(s)
        out[tkr] = {"sector": name, "close": ch["close"],
                    "day_change_pct": ch["day_change_pct"],
                    "five_day_pct": ch["five_day_pct"],
                    "month_pct": ch["month_pct"]}
    log.info("Sector ETFs: %d/%d resolved", len(out), len(SECTOR_ETFS))
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Regime classification
# ══════════════════════════════════════════════════════════════════════════════

def fetch_vix() -> float | None:
    """Latest VIX close via yfinance -- a key-free fallback for FRED's VIXCLS."""
    series = _close_series(["^VIX"], period="7d")
    s = series.get("^VIX")
    if s is None or s.empty:
        return None
    return round(float(s.iloc[-1]), 2)


def _vix_level(indicators: dict, vix_fallback: float | None) -> float | None:
    v = (indicators.get("VIXCLS") or {}).get("latest")
    return v if v is not None else vix_fallback


def _sector_leaders(sectors: dict, n: int = 3) -> list[str]:
    """Top-n sector tickers by 5-day performance."""
    ranked = sorted(
        ((t, d.get("five_day_pct")) for t, d in sectors.items()
         if d.get("five_day_pct") is not None),
        key=lambda x: x[1], reverse=True,
    )
    return [t for t, _ in ranked[:n]]


def classify_regime(indicators: dict, yield_curve: dict, sectors: dict,
                    vix_fallback: float | None = None) -> tuple[str, str]:
    """Return (regime, human-readable reasoning)."""
    vix = _vix_level(indicators, vix_fallback)
    spread = (indicators.get("T10Y2Y") or {}).get("latest")
    if spread is None:                       # fall back to Treasury curve
        if yield_curve.get("10Y") is not None and yield_curve.get("2Y") is not None:
            spread = round(yield_curve["10Y"] - yield_curve["2Y"], 3)

    leaders = _sector_leaders(sectors)
    leader_names = ", ".join(
        sectors.get(t, {}).get("sector", t) for t in leaders) or "n/a"

    ten_y      = indicators.get("DGS10") or {}
    ten_y_chg  = ten_y.get("pct_change_month")
    gold       = indicators.get("GOLDPMGBD228NLBM") or {}
    gold_chg   = gold.get("pct_change_month")
    energy_5d  = (sectors.get("XLE") or {}).get("five_day_pct")

    risk_on_sectors  = {"XLK", "XLY", "XLF"}
    risk_off_sectors = {"XLU", "XLP", "XLV"}
    leader_set = set(leaders)

    vix_s    = "n/a" if vix is None else f"{vix:.1f}"
    spread_s = "n/a" if spread is None else f"{spread:+.2f}"

    # --- inflation_concern: 10Y rising fast + gold strong + energy strong ----
    if (ten_y_chg is not None and ten_y_chg > 5
            and gold_chg is not None and gold_chg > 3
            and energy_5d is not None and energy_5d > 1):
        return "inflation_concern", (
            f"10Y yield up {ten_y_chg:+.1f}% over the past month, gold up "
            f"{gold_chg:+.1f}%, and energy (XLE) up {energy_5d:+.1f}% over 5 "
            f"days -- yields, gold and energy rising together points to an "
            f"inflation-driven tape.")

    # --- risk_on: low VIX + positive curve + cyclical leadership -------------
    if (vix is not None and vix < 18
            and spread is not None and spread > 0
            and leader_set & risk_on_sectors):
        return "risk_on", (
            f"VIX low at {vix_s} (<18), yield curve positive ({spread_s}), and "
            f"cyclical sectors lead (top 5-day: {leader_names}) -- a "
            f"risk-seeking backdrop.")

    # --- risk_off: high VIX + flat/inverted curve + defensive leadership -----
    if (vix is not None and vix > 25
            and spread is not None and spread < 0.2
            and leader_set & risk_off_sectors):
        return "risk_off", (
            f"VIX elevated at {vix_s} (>25), yield curve inverted or flat "
            f"({spread_s}), and defensives lead (top 5-day: {leader_names}) -- "
            f"a risk-averse backdrop.")

    # --- neutral -------------------------------------------------------------
    return "neutral", (
        f"No regime threshold cleanly met -- VIX {vix_s}, 10Y-2Y spread "
        f"{spread_s}, sector leadership: {leader_names}. Mixed signals, "
        f"classified neutral.")


# ══════════════════════════════════════════════════════════════════════════════
# Narrative
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_pct(v) -> str:
    return "n/a" if v is None else f"{v:+.2f}%"


def _data_digest(snap: dict) -> str:
    """Compact text digest of the snapshot for the LLM / template narrative."""
    ind = snap["economic_indicators"]
    idx = snap["global_indices"]
    sec = snap["sector_performance"]
    cry = snap["crypto_snapshot"]
    yc  = snap["yield_curve"]

    def ind_line(sid):
        d = ind.get(sid)
        if not d:
            return f"  {sid}: n/a"
        return (f"  {d['label']}: {d['latest']} "
                f"(wk {_fmt_pct(d['pct_change_week'])}, "
                f"mo {_fmt_pct(d['pct_change_month'])})")

    lines = ["ECONOMIC INDICATORS (FRED):"]
    lines += [ind_line(s) for s in FRED_SERIES] if ind else ["  (unavailable)"]

    lines.append("YIELD CURVE (Treasury): " + (
        ", ".join(f"{t}={yc[t]}" for t in TREASURY_TENORS.values()
                  if yc.get(t) is not None) or "unavailable"))

    lines.append("GLOBAL INDICES (close / 1d / 5d):")
    for t, d in idx.items():
        lines.append(f"  {d['name']}: {d['close']} "
                     f"({_fmt_pct(d['day_change_pct'])} / "
                     f"{_fmt_pct(d['five_day_pct'])})")
    if not idx:
        lines.append("  (unavailable)")

    lines.append("SECTOR ETFs (1d / 5d / 1mo):")
    for t, d in sorted(sec.items(),
                       key=lambda kv: kv[1].get("five_day_pct") or -999,
                       reverse=True):
        lines.append(f"  {d['sector']} ({t}): "
                     f"{_fmt_pct(d['day_change_pct'])} / "
                     f"{_fmt_pct(d['five_day_pct'])} / "
                     f"{_fmt_pct(d['month_pct'])}")
    if not sec:
        lines.append("  (unavailable)")

    lines.append("CRYPTO: " + (", ".join(
        f"{d['name']} {d['close']} ({_fmt_pct(d['change_24h_pct'])})"
        for d in cry.values()) or "unavailable"))

    cs = snap.get("commodity_signals")
    if cs and cs.get("signals"):
        tag = " (STALE — treat as background only)" if cs.get("stale") else ""
        lines.append(f"COMMODITY CROSS-SIGNALS{tag} (structural trend reads "
                     "from the commodities pipeline):")
        for name, s in cs["signals"].items():
            if s.get("status") == "OK":
                lines.append(f"  {name}: {s['read']} "
                             f"({s['commodity']} {s['trend_state']}) -- {s['why']}")
            else:
                lines.append(f"  {name}: unavailable")

    fed = snap["fed_calendar"]
    eco = snap["economic_calendar"]
    ern = snap["earnings_calendar"]
    lines.append(f"FED EVENTS (14d): " + (
        "; ".join(f"{e['title']} {e['date']}" for e in fed) or "none found"))
    lines.append(f"ECON RELEASES (this week): {len(eco)} item(s)"
                 + ("" if not eco else "; next: "
                    + (eco[0].get("event") or "n/a")))
    lines.append(f"EARNINGS (7d, S&P 1500): {len(ern)} name(s)"
                 + ("" if not ern else "; e.g. "
                    + ", ".join(e["symbol"] for e in ern[:8])))
    return "\n".join(lines)


def _template_narrative(snap: dict, regime: str, reasoning: str) -> str:
    """Deterministic 3-paragraph macro wrap (used when GROQ_API_KEY is absent)."""
    idx = snap["global_indices"]
    sec = snap["sector_performance"]

    def idx_bit(t):
        d = idx.get(t)
        return None if not d else f"{d['name']} {_fmt_pct(d['day_change_pct'])}"

    asia = ", ".join(b for b in (idx_bit("^N225"), idx_bit("^HSI"),
                                 idx_bit("000001.SS")) if b) or "Asian markets quiet"
    europe = ", ".join(b for b in (idx_bit("^FTSE"), idx_bit("^GDAXI")) if b) \
        or "European markets quiet"
    us = ", ".join(b for b in (idx_bit("SPY"), idx_bit("QQQ"),
                               idx_bit("IWM")) if b) or "US benchmarks flat"

    leaders = _sector_leaders(sec, 3)
    laggards = [t for t, _ in sorted(
        ((t, d.get("five_day_pct")) for t, d in sec.items()
         if d.get("five_day_pct") is not None),
        key=lambda x: x[1])[:3]]
    lead_s = ", ".join(sec[t]["sector"] for t in leaders) or "no clear leaders"
    lag_s  = ", ".join(sec[t]["sector"] for t in laggards) or "no clear laggards"

    fed = snap["fed_calendar"]
    ern = snap["earnings_calendar"]
    events = []
    if fed:
        events.append(fed[0]["title"])
    if snap["economic_calendar"]:
        events.append(snap["economic_calendar"][0].get("event") or "a macro release")
    if ern:
        events.append(f"{len(ern)} S&P 1500 earnings ({', '.join(e['symbol'] for e in ern[:5])}...)")
    events_s = "; ".join(events) if events else \
        "no major scheduled catalysts were detected"

    p1 = (f"Overnight wrap. {asia}. In Europe, {europe}. US benchmarks: {us}. "
          f"Over the past five sessions sector leadership has favored {lead_s}, "
          f"while {lag_s} lagged -- the rotation a read on where risk appetite "
          f"currently sits.")

    p2 = (f"Key events to watch: {events_s}. These are the scheduled catalysts "
          f"most likely to move the tape; everything else is positioning around "
          f"them.")

    p3 = (f"Day's likely character. The macro backdrop classifies as "
          f"'{regime.replace('_', ' ')}'. {reasoning} Expect price action "
          f"consistent with that regime unless an event above forces a repricing.")

    return f"{p1}\n\n{p2}\n\n{p3}"


def build_narrative(snap: dict, regime: str, reasoning: str) -> tuple[str, str]:
    """Return (narrative, method). Groq when available, else template."""
    digest = _data_digest(snap)
    if GROQ_API_KEY:
        try:
            from groq import Groq
            client = Groq(api_key=GROQ_API_KEY)
            prompt = (
                "You are a senior macro strategist writing the morning note for "
                "financial advisors. Using ONLY the data below, write EXACTLY "
                "three paragraphs, no headers, no bullet points:\n"
                "1) Overnight global wrap (Asia, Europe, US futures/benchmarks).\n"
                "2) Today's key events to watch.\n"
                "3) The day's likely market character, given the regime.\n\n"
                f"Current regime: {regime} -- {reasoning}\n\n"
                f"DATA:\n{digest}"
            )
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You are a senior macro "
                     "strategist at a wealth-management firm."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=700,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text, "groq"
        except Exception as exc:
            log.warning("Groq narrative failed: %s -- using template", exc)
    return _template_narrative(snap, regime, reasoning), "template"


# ══════════════════════════════════════════════════════════════════════════════
# Top upcoming event (next 24h)
# ══════════════════════════════════════════════════════════════════════════════

_IMPACT_RANK = {"high": 3, "3": 3, "medium": 2, "2": 2, "low": 1, "1": 1}


def pick_top_event(snap: dict) -> dict | None:
    """Most important *upcoming* catalyst in the next 24 hours, across calendars.

    Timed events (economic releases) are kept only when their timestamp is
    still ahead of us; date-only events (FOMC, earnings) are kept for today
    and tomorrow since their intraday time is not published here.
    """
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=24)
    today, tomorrow = now.date(), (now + timedelta(days=1)).date()
    candidates: list[dict] = []

    for e in snap["fed_calendar"]:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        if d in (today, tomorrow):
            candidates.append({"name": e["title"], "when": e["date"],
                               "source": "Federal Reserve", "rank": 3,
                               "sort": e["date"]})

    for e in snap["economic_calendar"]:
        raw = e.get("time") or ""
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if dt is None or not (now <= dt <= horizon):
            continue
        rank = _IMPACT_RANK.get(str(e.get("impact", "")).lower(), 2)
        candidates.append({"name": e.get("event") or "Economic release",
                           "when": raw,
                           "source": f"Economic calendar ({e.get('country','?')})",
                           "rank": rank, "sort": raw})

    for e in snap["earnings_calendar"]:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError, TypeError):
            continue
        if d in (today, tomorrow):
            candidates.append({"name": f"{e['symbol']} earnings",
                               "when": f"{e['date']} ({e.get('hour') or 'time TBD'})",
                               "source": "Earnings calendar", "rank": 1,
                               "sort": e["date"]})

    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c["rank"], str(c["sort"])))
    return candidates[0]


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run() -> None:
    log.info("Macro Context Agent -- collecting macro snapshot")
    snapshot_time = datetime.now(timezone.utc)

    snap = {
        "economic_indicators": fetch_economic_indicators(),
        "yield_curve":         fetch_yield_curve(),
        "fed_calendar":        fetch_fed_calendar(),
        "economic_calendar":   fetch_economic_calendar(),
        "earnings_calendar":   fetch_earnings_calendar(),
        "global_indices":      fetch_global_indices(),
        "crypto_snapshot":     fetch_crypto(),
        "sector_performance":  fetch_sector_performance(),
    }

    # Commodity cross-link (copper -> growth, gold -> risk, oil -> inflation).
    # Strictly additive context: it colors the reasoning and the narrative
    # digest but never overrides the regime thresholds. Missing/stale scans
    # degrade to "no commodity layer", not to neutral evidence.
    try:
        from commodities.cross_link import latest_signals, summarize as cs_summary
        commodity_signals = latest_signals()
    except Exception as exc:  # noqa: BLE001
        log.warning("commodity cross-link unavailable: %s", exc)
        commodity_signals, cs_summary = None, None
    snap["commodity_signals"] = commodity_signals

    regime, reasoning = classify_regime(
        snap["economic_indicators"], snap["yield_curve"],
        snap["sector_performance"], vix_fallback=fetch_vix(),
    )
    if commodity_signals and commodity_signals.get("signals"):
        tag = "stale commodity cross-read (background only)" \
            if commodity_signals.get("stale") else "Commodity cross-read"
        reasoning += f" {tag}: {cs_summary(commodity_signals)}."
    log.info("Regime classified: %s", regime.upper())

    narrative, method = build_narrative(snap, regime, reasoning)
    log.info("Narrative generated via: %s", method)

    top_event = pick_top_event(snap)

    # ── Persist ───────────────────────────────────────────────────────────────
    row = {
        "snapshot_time":         snapshot_time.isoformat(),
        "economic_indicators":   snap["economic_indicators"],
        "yield_curve":           snap["yield_curve"],
        "fed_calendar":          snap["fed_calendar"],
        "economic_calendar":     snap["economic_calendar"],
        "earnings_calendar":     snap["earnings_calendar"],
        "global_indices":        snap["global_indices"],
        "crypto_snapshot":       snap["crypto_snapshot"],
        "sector_performance":    snap["sector_performance"],
        "commodity_signals":     commodity_signals,
        "regime_classification": regime,
        "narrative_summary":     narrative,
    }
    try:
        db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        resp = db.table("macro_context").insert(row).execute()
        rid = resp.data[0]["id"] if resp.data else "?"
        log.info("macro_context row stored (id=%s)", rid)
    except Exception as exc:
        log.warning("macro_context insert skipped/failed: %s", exc)
        log.warning("Apply supabase/migrations/003_macro_context.sql to persist.")

    # ── Report to console ─────────────────────────────────────────────────────
    bar = "=" * 70
    print(f"\n{bar}\nMACRO CONTEXT -- {snapshot_time.strftime('%Y-%m-%d %H:%M UTC')}\n{bar}")

    print(f"\n[ REGIME CLASSIFICATION ]\n  {regime.upper()}\n  Reasoning: {reasoning}")

    print(f"\n[ 3-PARAGRAPH MACRO NARRATIVE ]  (via {method})\n")
    for para in narrative.split("\n\n"):
        print("  " + para.replace("\n", "\n  ") + "\n")

    print("[ MOST IMPORTANT EVENT -- NEXT 24 HOURS ]")
    if top_event:
        print(f"  {top_event['name']}")
        print(f"  When:   {top_event['when']}")
        print(f"  Source: {top_event['source']}")
    else:
        print("  No scheduled catalyst detected in the next 24 hours")
        print("  (FRED data is rear-looking; Fed/Finnhub calendars may be empty "
              "if a key or endpoint is unavailable -- see warnings above).")
    print(bar + "\n")


if __name__ == "__main__":
    run()
