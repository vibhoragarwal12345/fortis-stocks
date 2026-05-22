"""
Risk Flagger
"What happened to stocks you already own" -- the highest-priority story for a
wealth-management firm, surfaced before any new-idea recommendations.

For each user portfolio, for each holding, sweep every available risk source
and emit one alert per (alert_type) when a material event is detected:

  earnings    Finnhub earnings calendar -- reported in last 24h, or upcoming in next 7d
  sec_event   8-K filings on sec_filings in the last 48h
  analyst     Finnhub recommendation-trend shift (>=2 net buy ratings)
  news        ticker_sentiment_rollup -- volume > 5 AND |weighted_sentiment| > 0.3
  anomaly     anomaly_flags -- gap, breakdown, ma_crossover, range_expansion, etc.
  insider     anomaly_flags -- insider_cluster / institutional_pivot, OR a raw Form 4

Each alert is classified into one of four severities:

  CRITICAL       8-K Item 5.02 / 4.01, bankruptcy, going-concern, regulatory action
  HIGH           earnings miss, downgrade, breakdown_52w, unusual put activity
  MEDIUM         negative news cluster, MA breakdown, sector weakness in holding
  INFORMATIONAL  upcoming earnings, ex-dividend, expected event, mild flag

Item-code text is matched against sec_filings.description (the EDGAR title --
sec_watcher does not pull per-filing item indexes; see catalyst_agent
LIMITATIONS).

Outputs:
  * holdings_alerts          -- one row per (portfolio, ticker, alert_type)
  * portfolio_alert_summary  -- severity counts + portfolio_at_risk_pct +
                                top_3_priorities

Usage:
    python pipeline/agents/risk_flagger.py [run_type]   # default midday
    python pipeline/agents/risk_flagger.py midday demo  # demo-user only, verbose
"""

import logging
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (  # noqa: E402
    FINNHUB_API_KEY, GROQ_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL,
)
from supabase import create_client  # noqa: E402

DEMO_EMAIL          = "demo@fortis.local"
HTTP_TIMEOUT        = 20
SEC_WINDOW_HOURS    = 48
EARNINGS_PAST_DAYS  = 1
EARNINGS_FWD_DAYS   = 7
NEWS_VOLUME_MIN     = 5
NEWS_ABS_SENT_MIN   = 0.3
REC_THROTTLE        = 1.1
REC_SHIFT_MIN       = 2
PUT_CALL_UNUSUAL    = 1.8
GROQ_MODEL          = "llama-3.3-70b-versatile"
GROQ_WORKERS        = 6

# Keywords matched against sec_filings.description to escalate 8-Ks to CRITICAL.
CRITICAL_8K_PATTERNS = [
    (re.compile(r"\b(bankruptcy|chapter 11|chapter 7)\b", re.I),       "bankruptcy filing"),
    (re.compile(r"\bgoing concern\b", re.I),                            "going-concern doubt"),
    (re.compile(r"\b(item\s*5\.02|departure|appointment of officer)\b", re.I),
                                                                        "executive change (Item 5.02)"),
    (re.compile(r"\b(item\s*4\.01|change in registrant'?s certifying accountant|auditor)\b", re.I),
                                                                        "auditor change (Item 4.01)"),
    (re.compile(r"\b(sec investigation|enforcement action|cease and desist|consent decree|"
                r"deficiency|delisting)\b", re.I),
                                                                        "regulatory action"),
    (re.compile(r"\b(restate|restated|restatement)\b", re.I),            "financial restatement"),
]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _fetch_all(query_fn, page: int = 1000) -> list[dict]:
    rows, start = [], 0
    while True:
        chunk = query_fn().range(start, start + page - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        start += page


# ══════════════════════════════════════════════════════════════════════════════
# Source loaders -- all keyed by ticker so per-portfolio scanning is O(holdings)
# ══════════════════════════════════════════════════════════════════════════════

def _load_portfolios(db, demo_only: bool):
    if demo_only:
        try:
            existing = db.auth.admin.list_users()
            users = existing if isinstance(existing, list) else getattr(existing, "users", [])
            demo_id = None
            for u in users:
                email = getattr(u, "email", None) or u.get("email")
                if email == DEMO_EMAIL:
                    demo_id = str(getattr(u, "id", None) or u.get("id"))
                    break
            if demo_id:
                return (db.table("portfolios").select("id,name,user_id")
                        .eq("user_id", demo_id).execute().data or [])
        except Exception as exc:
            log.warning("demo-user lookup failed (%s) -- falling back to all portfolios", exc)
    return db.table("portfolios").select("id,name,user_id").execute().data or []


def _load_holdings(db, portfolio_id):
    return (db.table("portfolio_holdings")
            .select("ticker,shares,cost_basis")
            .eq("portfolio_id", portfolio_id).execute().data or [])


def _load_earnings(tickers: set[str]) -> dict[str, dict]:
    if not FINNHUB_API_KEY:
        return {}
    today = datetime.now(timezone.utc).date()
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": (today - timedelta(days=EARNINGS_PAST_DAYS)).isoformat(),
                    "to":   (today + timedelta(days=EARNINGS_FWD_DAYS)).isoformat(),
                    "token": FINNHUB_API_KEY},
            timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        out = {}
        for it in resp.json().get("earningsCalendar", []) or []:
            sym = (it.get("symbol") or "").upper()
            if sym and (not tickers or sym in tickers):
                out[sym] = it
        log.info("earnings calendar: %d hits in holdings", len(out))
        return out
    except Exception as exc:
        log.warning("earnings calendar failed: %s", exc)
        return {}


def _load_sec(db, tickers: set[str]) -> dict[str, list[dict]]:
    if not tickers:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=SEC_WINDOW_HOURS)).isoformat()
    rows = _fetch_all(lambda: db.table("sec_filings")
                      .select("ticker,form_type,filing_date,filing_url,description")
                      .in_("ticker", list(tickers))
                      .gte("filing_date", cutoff))
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("ticker"):
            out[r["ticker"]].append(r)
    return out


def _load_sentiment(db, tickers: set[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    rows = _fetch_all(lambda: db.table("ticker_sentiment_rollup")
                      .select("ticker,weighted_sentiment,sentiment_volume,"
                              "sentiment_velocity,top_3_bearish,top_3_bullish,"
                              "snapshot_date,run_type")
                      .in_("ticker", list(tickers))
                      .order("snapshot_date", desc=True))
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["ticker"], r)
    return out


def _load_anomalies(db, tickers: set[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    flags = _fetch_all(lambda: db.table("anomaly_flags")
                       .select("ticker,flag_type,flag_value,severity,context,"
                               "snapshot_time")
                       .in_("ticker", list(tickers))
                       .order("snapshot_time", desc=True))
    latest = flags[0]["snapshot_time"] if flags else None
    out: dict[str, dict] = defaultdict(dict)
    for f in flags:
        if f["snapshot_time"] != latest or not f.get("ticker"):
            continue
        cur = out[f["ticker"]].get(f["flag_type"])
        if cur is None or (_num(f.get("severity")) or 0) > (_num(cur.get("severity")) or 0):
            out[f["ticker"]][f["flag_type"]] = f
    return out


def _load_options(db, tickers: set[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    try:
        rows = _fetch_all(lambda: db.table("options_signals")
                          .select("ticker,put_call_ratio,unusual_put_strikes,"
                                  "large_block_alerts,iv_skew,snapshot_time")
                          .in_("ticker", list(tickers))
                          .order("snapshot_time", desc=True))
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["ticker"], r)
    return out


def _load_recommendations(tickers: list[str]) -> dict[str, dict]:
    if not FINNHUB_API_KEY or not tickers:
        return {}
    out: dict[str, dict] = {}
    for t in tickers:
        try:
            resp = requests.get("https://finnhub.io/api/v1/stock/recommendation",
                                params={"symbol": t, "token": FINNHUB_API_KEY},
                                timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json() or []
                if len(data) >= 2:
                    cur, prev = data[0], data[1]
                    cur_bull  = cur.get("strongBuy", 0) + cur.get("buy", 0)
                    prev_bull = prev.get("strongBuy", 0) + prev.get("buy", 0)
                    shift = cur_bull - prev_bull
                    if abs(shift) >= REC_SHIFT_MIN:
                        out[t] = {"shift": shift, "current": cur, "previous": prev}
        except Exception as exc:
            log.debug("recommendation %s failed: %s", t, exc)
        time.sleep(REC_THROTTLE)
    log.info("analyst recommendations: %d ticker(s) had material shifts", len(out))
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Portfolio weighting -- needed for portfolio_at_risk_pct
# ══════════════════════════════════════════════════════════════════════════════

def _current_prices(tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}
    try:
        raw = yf.download(tickers, period="5d", interval="1d",
                          auto_adjust=True, progress=False,
                          group_by="ticker", threads=False)
    except Exception as exc:
        log.warning("yfinance fetch failed: %s -- weights will be share-equal", exc)
        return {}
    import pandas as pd
    multi = isinstance(raw.columns, pd.MultiIndex)
    out = {}
    for t in tickers:
        try:
            s = (raw[t]["Close"] if multi else raw["Close"]).dropna()
            if len(s):
                out[t] = float(s.iloc[-1])
        except (KeyError, TypeError):
            pass
    return out


def _portfolio_weights(holdings: list[dict], prices: dict[str, float]) -> dict[str, float]:
    values = {h["ticker"]: prices.get(h["ticker"], 0.0) * float(h["shares"])
              for h in holdings}
    total = sum(values.values())
    if total <= 0:
        # Fallback: equal weight so the report still says something useful.
        n = len(holdings) or 1
        return {h["ticker"]: 1.0 / n for h in holdings}
    return {t: v / total for t, v in values.items()}


# ══════════════════════════════════════════════════════════════════════════════
# Severity classification
# ══════════════════════════════════════════════════════════════════════════════

def _classify_sec(filings: list[dict]) -> tuple[str, str, dict]:
    """Returns (severity, headline, supporting). Highest-severity 8-K wins."""
    eightk = [f for f in filings if f.get("form_type") in ("8-K", "8-K/A")]
    if not eightk:
        return ("INFORMATIONAL", "filing", {"count": len(filings)})
    eightk.sort(key=lambda f: f.get("filing_date") or "", reverse=True)
    most_recent = eightk[0]
    desc = most_recent.get("description") or ""
    for pat, label in CRITICAL_8K_PATTERNS:
        if pat.search(desc):
            return ("CRITICAL", label, {
                "filing_date": most_recent.get("filing_date"),
                "filing_url":  most_recent.get("filing_url"),
                "description": desc[:240],
                "item_match":  label,
                "count":       len(eightk),
            })
    return ("HIGH", "material corporate event", {
        "filing_date": most_recent.get("filing_date"),
        "filing_url":  most_recent.get("filing_url"),
        "description": desc[:240],
        "count":       len(eightk),
    })


def _classify_earnings(e: dict) -> tuple[str, str, dict]:
    d = _parse_dt(e.get("date"))
    today = datetime.now(timezone.utc).date()
    reported = d is not None and d.date() <= today and e.get("epsActual") is not None
    eps_a  = _num(e.get("epsActual"))
    eps_e  = _num(e.get("epsEstimate"))
    rev_a  = _num(e.get("revenueActual"))
    rev_e  = _num(e.get("revenueEstimate"))
    payload = {
        "date":              e.get("date"),
        "hour":              e.get("hour"),
        "eps_estimate":      eps_e,
        "eps_actual":        eps_a,
        "revenue_estimate":  rev_e,
        "revenue_actual":    rev_a,
        "reported":          reported,
    }
    if reported and eps_e is not None and eps_a is not None:
        beat = eps_a >= eps_e
        if not beat:
            payload["surprise_pct"] = ((eps_a - eps_e) / abs(eps_e)) * 100 if eps_e else None
            return ("HIGH", "earnings miss", payload)
        return ("INFORMATIONAL", "earnings beat", payload)
    return ("INFORMATIONAL", "earnings upcoming", payload)


def _classify_analyst(rec: dict) -> tuple[str, str, dict]:
    shift = rec.get("shift", 0)
    payload = {"shift": shift, "current": rec.get("current"),
               "previous": rec.get("previous")}
    if shift <= -REC_SHIFT_MIN:
        return ("HIGH", "analyst downgrade", payload)
    return ("INFORMATIONAL", "analyst upgrade", payload)


def _classify_news(s: dict) -> tuple[str, str, dict] | None:
    vol = _num(s.get("sentiment_volume")) or 0
    sent = _num(s.get("weighted_sentiment"))
    if vol < NEWS_VOLUME_MIN or sent is None or abs(sent) < NEWS_ABS_SENT_MIN:
        return None
    payload = {
        "weighted_sentiment": sent,
        "sentiment_volume":   vol,
        "sentiment_velocity": _num(s.get("sentiment_velocity")),
        "top_bearish":        s.get("top_3_bearish"),
        "top_bullish":        s.get("top_3_bullish"),
    }
    if sent < 0:
        return ("MEDIUM", "negative news cluster", payload)
    # Strong positive flows are reported as informational -- not a risk, but
    # worth surfacing to advisors so they can preempt client questions.
    return ("INFORMATIONAL", "positive news cluster", payload)


def _classify_anomaly(flags: dict) -> tuple[str, str, dict] | None:
    if not flags:
        return None
    # Highest-severity, narratively-meaningful flag wins. breakdown_52w is
    # always a HIGH for an existing holding.
    severity_map = {
        "breakdown_52w":     ("HIGH",   "breakdown below 52-week support"),
        "ma_crossover":      ("MEDIUM", "moving-average breakdown"),
        "gap_anomaly":       ("MEDIUM", "abnormal gap"),
        "range_expansion":   ("MEDIUM", "range expansion"),
        "price_acceleration":("MEDIUM", "price acceleration"),
        "breakout_52w":      ("INFORMATIONAL", "52-week breakout"),
    }
    best = None
    for ft, f in flags.items():
        if ft not in severity_map:
            continue
        sev, label = severity_map[ft]
        rank = {"HIGH": 3, "MEDIUM": 2, "INFORMATIONAL": 1}.get(sev, 0)
        cand = (rank, ft, sev, label, f)
        if best is None or cand > best:
            best = cand
    if not best:
        return None
    _, ft, sev, label, f = best
    return (sev, label, {
        "flag_type":  ft,
        "flag_value": _num(f.get("flag_value")),
        "severity":   _num(f.get("severity")),
        "context":    f.get("context"),
    })


def _classify_insider(flags: dict) -> tuple[str, str, dict] | None:
    if not flags:
        return None
    if "insider_cluster" in flags:
        f = flags["insider_cluster"]
        ctx = f.get("context") or {}
        sells = ctx.get("sell_count") or 0
        buys  = ctx.get("buy_count") or 0
        # Cluster of sells in a held position is a MEDIUM warning.
        sev = "MEDIUM" if sells > buys else "INFORMATIONAL"
        label = "insider selling cluster" if sells > buys else "insider buying cluster"
        return (sev, label, {"flag_value": _num(f.get("flag_value")),
                             "context": ctx})
    if "institutional_pivot" in flags:
        f = flags["institutional_pivot"]
        return ("INFORMATIONAL", "institutional positioning shift",
                {"flag_value": _num(f.get("flag_value")),
                 "context": f.get("context")})
    return None


def _classify_options(o: dict) -> tuple[str, str, dict] | None:
    """Unusual put activity in a holding is a HIGH signal."""
    if not o:
        return None
    pcr = _num(o.get("put_call_ratio"))
    unusual_puts = o.get("unusual_put_strikes") or []
    if (pcr is not None and pcr >= PUT_CALL_UNUSUAL) or len(unusual_puts) >= 3:
        return ("HIGH", "unusual put activity", {
            "put_call_ratio":    pcr,
            "unusual_put_count": len(unusual_puts),
            "iv_skew":           _num(o.get("iv_skew")),
            "top_put_strikes":   unusual_puts[:3],
        })
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Message generation -- Groq when available, otherwise per-type template
# ══════════════════════════════════════════════════════════════════════════════

_GROQ_SYSTEM = (
    "You write one-sentence risk alerts for a financial advisor about a stock "
    "they ALREADY OWN. State the event, the implication for the holding, and "
    "the most important number. Tone: concise, factual, advisor-to-advisor. "
    "Never speculate or give buy/sell advice. Exactly one sentence."
)


def _template_message(ticker: str, weight_pct: float, severity: str,
                      alert_type: str, label: str, data: dict) -> str:
    w = f"{weight_pct:.1f}%"
    if alert_type == "sec_event":
        when = (data.get("filing_date") or "")[:10] or "recently"
        return (f"{ticker} ({w} of portfolio): filed an 8-K on {when} "
                f"-- {label}.")

    if alert_type == "earnings":
        when = data.get("date") or "this week"
        if data.get("reported"):
            eps_a, eps_e = data.get("eps_actual"), data.get("eps_estimate")
            if eps_a is not None and eps_e is not None:
                miss = eps_a < eps_e
                verb = "missed" if miss else "beat"
                return (f"{ticker} ({w}): reported earnings {when} -- EPS "
                        f"${eps_a} vs estimate ${eps_e}, {verb} by "
                        f"{abs(eps_a-eps_e):.2f}.")
            return f"{ticker} ({w}): reported earnings on {when}."
        slot = {"bmo": " (before the open)", "amc": " (after the close)"}.get(
            data.get("hour"), "")
        eps_e = data.get("eps_estimate")
        est = f", consensus EPS ${eps_e}" if eps_e is not None else ""
        return f"{ticker} ({w}): earnings due {when}{slot}{est}."

    if alert_type == "analyst":
        shift = data.get("shift", 0)
        direction = "downgrade" if shift < 0 else "upgrade"
        cur = data.get("current") or {}
        return (f"{ticker} ({w}): analyst {direction} -- net {shift:+d} buy "
                f"ratings this month (now {cur.get('strongBuy',0)} strong-buy / "
                f"{cur.get('buy',0)} buy / {cur.get('hold',0)} hold / "
                f"{cur.get('sell',0)} sell).")

    if alert_type == "news":
        sent = data.get("weighted_sentiment") or 0
        vol  = int(data.get("sentiment_volume") or 0)
        bucket = data.get("top_bearish") if sent < 0 else data.get("top_bullish")
        headline = ""
        if isinstance(bucket, list) and bucket:
            headline = (bucket[0] or {}).get("headline") or ""
        tone = "negative" if sent < 0 else "positive"
        ex = f' e.g. "{headline[:80]}".' if headline else ""
        return (f"{ticker} ({w}): {tone} news cluster -- {vol} articles, "
                f"weighted sentiment {sent:+.2f}.{ex}")

    if alert_type == "anomaly":
        sev = data.get("severity")
        fv  = data.get("flag_value")
        tail = ""
        if fv is not None:
            tail = f" (flag value {fv:+.2f})" if isinstance(fv, float) else ""
        sev_s = f" severity {sev:.0f}/100" if isinstance(sev, (int, float)) else ""
        return f"{ticker} ({w}): {label}{tail}{sev_s}."

    if alert_type == "insider":
        ctx = data.get("context") or {}
        sells = ctx.get("sell_count")
        buys  = ctx.get("buy_count")
        if sells is not None and buys is not None:
            return (f"{ticker} ({w}): {label} -- {sells} sell / {buys} buy "
                    f"Form 4 filings in the last 5 days.")
        return f"{ticker} ({w}): {label}."

    if alert_type == "options":
        pcr = data.get("put_call_ratio")
        n   = data.get("unusual_put_count") or 0
        pcr_s = f", put/call ratio {pcr:.2f}" if isinstance(pcr, (int, float)) else ""
        return (f"{ticker} ({w}): {label} -- {n} unusual put strike(s){pcr_s}.")

    return f"{ticker} ({w}): {label}."


def _groq_message(ticker: str, weight_pct: float, severity: str,
                   alert_type: str, label: str, data: dict) -> str | None:
    if not GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        user_prompt = (
            f"Ticker: {ticker}\n"
            f"Portfolio weight: {weight_pct:.1f}%\n"
            f"Severity: {severity}\n"
            f"Alert type: {alert_type} -- {label}\n"
            f"Supporting data: {data}\n\n"
            f'Required format: "{ticker} ({weight_pct:.1f}% of portfolio): '
            f'<event and implication, one sentence>."'
        )
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": _GROQ_SYSTEM},
                      {"role": "user",   "content": user_prompt}],
            temperature=0.2, max_tokens=110)
        msg = (resp.choices[0].message.content or "").strip()
        return msg or None
    except Exception as exc:
        log.debug("Groq message failed for %s/%s: %s", ticker, alert_type, exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Per-portfolio scan
# ══════════════════════════════════════════════════════════════════════════════

def _scan_portfolio(portfolio: dict, holdings: list[dict],
                    weights: dict[str, float], src: dict) -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    alerts: list[dict] = []

    for h in holdings:
        t = h["ticker"]
        w_pct = weights.get(t, 0.0) * 100

        candidates: list[tuple[str, str, str, dict]] = []  # (alert_type, severity, label, data)

        if t in src["sec"]:
            sev, label, data = _classify_sec(src["sec"][t])
            candidates.append(("sec_event", sev, label, data))

        if t in src["earnings"]:
            sev, label, data = _classify_earnings(src["earnings"][t])
            candidates.append(("earnings", sev, label, data))

        if t in src["recommendations"]:
            sev, label, data = _classify_analyst(src["recommendations"][t])
            candidates.append(("analyst", sev, label, data))

        if t in src["sentiment"]:
            news = _classify_news(src["sentiment"][t])
            if news:
                sev, label, data = news
                candidates.append(("news", sev, label, data))

        anom = _classify_anomaly(src["anomalies"].get(t, {}))
        if anom:
            sev, label, data = anom
            candidates.append(("anomaly", sev, label, data))

        ins = _classify_insider(src["anomalies"].get(t, {}))
        if ins:
            sev, label, data = ins
            candidates.append(("insider", sev, label, data))

        opt = _classify_options(src["options"].get(t))
        if opt:
            sev, label, data = opt
            candidates.append(("options", sev, label, data))

        # Smart Money pattern alerts (Step 6.7). Only HIGH/MEDIUM confidence
        # patterns can drive holdings alerts; LOW is informational only.
        sm = src.get("smart_money", {}).get(t)
        if sm and sm.get("confidence_level") in ("HIGH", "MEDIUM"):
            pat = sm.get("pattern_detected")
            if pat == "VALUE_TRAP_WARNING":
                candidates.append(("smart_money", "HIGH",
                    "smart-money pattern VALUE_TRAP_WARNING -- bearish across "
                    "insider, short, revisions despite recent beat",
                    {"pattern": pat,
                     "composite": sm.get("composite_smart_money_score"),
                     "confidence": sm.get("confidence_level"),
                     "insider_cluster": sm.get("insider_detected_cluster"),
                     "short_trend": sm.get("short_trend_direction")}))
            elif pat == "QUIET_DISTRIBUTION":
                candidates.append(("smart_money", "HIGH",
                    "smart-money pattern QUIET_DISTRIBUTION -- insiders exiting, "
                    "shorts building, estimates drifting down",
                    {"pattern": pat,
                     "composite": sm.get("composite_smart_money_score"),
                     "confidence": sm.get("confidence_level"),
                     "insider_cluster": sm.get("insider_detected_cluster"),
                     "short_trend": sm.get("short_trend_direction")}))
            elif pat == "INSIDER_SMOKE_SIGNAL":
                candidates.append(("smart_money", "HIGH",
                    "smart-money pattern INSIDER_SMOKE_SIGNAL -- public bullish "
                    "but insiders selling discretionary",
                    {"pattern": pat,
                     "composite": sm.get("composite_smart_money_score"),
                     "confidence": sm.get("confidence_level"),
                     "insider_cluster": sm.get("insider_detected_cluster")}))

        for alert_type, severity, label, data in candidates:
            alerts.append({
                "portfolio_id":    portfolio["id"],
                "ticker":          t,
                "alert_date":      today,
                "run_type":        src["run_type"],
                "severity":        severity,
                "alert_type":      alert_type,
                "_label":          label,
                "_weight_pct":     w_pct,
                "supporting_data": data,
                "requires_action": severity in ("CRITICAL", "HIGH"),
            })
    return alerts


def _attach_messages(alerts: list[dict]) -> None:
    """Generate one message per alert -- Groq in parallel, template fallback."""
    def _make(alert):
        msg = _groq_message(alert["ticker"], alert["_weight_pct"],
                            alert["severity"], alert["alert_type"],
                            alert["_label"], alert["supporting_data"])
        if msg is None:
            msg = _template_message(alert["ticker"], alert["_weight_pct"],
                                    alert["severity"], alert["alert_type"],
                                    alert["_label"], alert["supporting_data"])
        alert["message"] = msg

    if GROQ_API_KEY and alerts:
        with ThreadPoolExecutor(max_workers=GROQ_WORKERS) as pool:
            list(pool.map(_make, alerts))
    else:
        for a in alerts:
            _make(a)


def _summarize_portfolio(portfolio: dict, alerts: list[dict],
                          weights: dict[str, float], run_type: str) -> dict:
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "INFORMATIONAL": 0}
    tickers_at_risk: set[str] = set()
    for a in alerts:
        counts[a["severity"]] = counts.get(a["severity"], 0) + 1
        if a["severity"] in ("CRITICAL", "HIGH"):
            tickers_at_risk.add(a["ticker"])
    at_risk_pct = sum(weights.get(t, 0.0) for t in tickers_at_risk)

    sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "INFORMATIONAL": 3}
    ranked = sorted(alerts, key=lambda a: (sev_order.get(a["severity"], 9),
                                            -a["_weight_pct"]))
    top3 = []
    seen_tickers: set[str] = set()
    for a in ranked:
        if a["ticker"] in seen_tickers:
            continue
        seen_tickers.add(a["ticker"])
        top3.append({
            "ticker":     a["ticker"],
            "severity":   a["severity"],
            "alert_type": a["alert_type"],
            "weight_pct": round(a["_weight_pct"], 2),
            "message":    a["message"],
        })
        if len(top3) == 3:
            break

    return {
        "portfolio_id":          portfolio["id"],
        "snapshot_date":         datetime.now(timezone.utc).date().isoformat(),
        "run_type":              run_type,
        "critical_count":        counts["CRITICAL"],
        "high_count":            counts["HIGH"],
        "medium_count":          counts["MEDIUM"],
        "info_count":            counts["INFORMATIONAL"],
        "portfolio_at_risk_pct": round(at_risk_pct, 4),
        "top_3_priorities":      top3,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Persistence
# ══════════════════════════════════════════════════════════════════════════════

def _persist_alerts(db, alerts: list[dict]) -> int:
    if not alerts:
        return 0
    payload = [{k: v for k, v in a.items() if not k.startswith("_")}
               for a in alerts]
    try:
        db.table("holdings_alerts").upsert(
            payload,
            on_conflict="portfolio_id,ticker,alert_date,run_type,alert_type"
        ).execute()
        return len(payload)
    except Exception as exc:
        log.warning("holdings_alerts not persisted (%s) -- apply migration 024",
                    getattr(exc, "code", exc))
        return 0


def _persist_summary(db, summary: dict) -> bool:
    try:
        db.table("portfolio_alert_summary").upsert(
            [summary], on_conflict="portfolio_id,snapshot_date,run_type"
        ).execute()
        return True
    except Exception as exc:
        log.warning("portfolio_alert_summary not persisted (%s) -- apply migration 024",
                    getattr(exc, "code", exc))
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════════

def _report(portfolio: dict, alerts: list[dict], summary: dict) -> None:
    bar = "=" * 78
    print(f"\n{bar}\nRISK FLAGGER  --  {portfolio['name']}\n{bar}")

    print(f"\n  critical: {summary['critical_count']:>3}    "
          f"high: {summary['high_count']:>3}    "
          f"medium: {summary['medium_count']:>3}    "
          f"info: {summary['info_count']:>3}")
    print(f"  portfolio_at_risk_pct : "
          f"{summary['portfolio_at_risk_pct']*100:5.1f}%   "
          f"(sum of weights of holdings with CRITICAL or HIGH alerts)")

    crit_high = [a for a in alerts if a["severity"] in ("CRITICAL", "HIGH")]
    if crit_high:
        crit_high.sort(key=lambda a: ({"CRITICAL": 0, "HIGH": 1}[a["severity"]],
                                       -a["_weight_pct"]))
        print(f"\n[ CRITICAL + HIGH ALERTS  --  {len(crit_high)} ]")
        for a in crit_high:
            print(f"  {a['severity']:<9} {a['ticker']:<6} "
                  f"({a['_weight_pct']:5.1f}%)  {a['alert_type']:<10}  "
                  f"{a['_label']}")
            print(f"             {a['message']}")
    else:
        print("\n[ CRITICAL + HIGH ALERTS  --  none ]")

    print(f"\n[ TOP 3 PRIORITIES TO ADDRESS TODAY ]")
    if not summary["top_3_priorities"]:
        print("  (no alerts surfaced)")
    for i, p in enumerate(summary["top_3_priorities"], 1):
        print(f"  {i}. {p['severity']:<9} {p['ticker']:<6} "
              f"({p['weight_pct']:.1f}%)  {p['alert_type']}")
        print(f"     {p['message']}")
    print(f"\n{bar}\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday", demo_only: bool = False) -> None:
    if run_type not in ("premarket", "midday", "close"):
        run_type = "midday"
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Risk Flagger -- run_type=%s  demo_only=%s  llm=%s",
             run_type, demo_only, "groq" if GROQ_API_KEY else "template")

    portfolios = _load_portfolios(db, demo_only)
    if not portfolios:
        log.warning("No portfolios -- run pipeline/data/load_sample_portfolio.py")
        return
    log.info("Scanning %d portfolio(s)", len(portfolios))

    # Pre-load every shared source ONCE for the union of all held tickers.
    all_holdings: dict[str, list[dict]] = {}
    union: set[str] = set()
    for p in portfolios:
        hs = _load_holdings(db, p["id"])
        all_holdings[p["id"]] = hs
        union.update(h["ticker"] for h in hs)
    log.info("Union of holdings: %d distinct ticker(s)", len(union))

    # Smart Money intel (Step 6.7) -- pre-load for portfolio tickers so we
    # can raise a HIGH-severity alert when a HELD position's pattern flips to
    # VALUE_TRAP_WARNING or QUIET_DISTRIBUTION.
    smart_money: dict[str, dict] = {}
    try:
        sm_rows = (db.table("smart_money_intel")
                   .select("ticker,pattern_detected,confidence_level,"
                           "composite_smart_money_score,insider_detected_cluster,"
                           "short_trend_direction,quality_grade,snapshot_date")
                   .in_("ticker", sorted(union))
                   .order("snapshot_date", desc=True).execute().data or [])
        for r in sm_rows:
            smart_money.setdefault(r["ticker"], r)
    except Exception as exc:
        log.debug("smart_money_intel preload failed: %s", exc)

    src = {
        "run_type":     run_type,
        "earnings":     _load_earnings(union),
        "sec":          _load_sec(db, union),
        "sentiment":    _load_sentiment(db, union),
        "anomalies":    _load_anomalies(db, union),
        "options":      _load_options(db, union),
        "smart_money":  smart_money,
    }
    # Recommendations are per-ticker Finnhub calls; skip for tickers that
    # already have a CRITICAL signal coming from SEC or a definitive earnings
    # surprise -- those override anyway.
    rec_needed = [t for t in union
                  if t not in src["sec"] or
                  not any(f.get("form_type") in ("8-K", "8-K/A")
                          for f in src["sec"].get(t, []))]
    src["recommendations"] = _load_recommendations(rec_needed)

    prices = _current_prices(sorted(union))

    for p in portfolios:
        holdings = all_holdings[p["id"]]
        if not holdings:
            log.warning("portfolio %s has no holdings -- skipping", p["name"])
            continue
        weights = _portfolio_weights(holdings, prices)
        alerts  = _scan_portfolio(p, holdings, weights, src)
        _attach_messages(alerts)
        summary = _summarize_portfolio(p, alerts, weights, run_type)

        n_persisted = _persist_alerts(db, alerts)
        log.info("%s: %d alerts persisted (%d generated)",
                 p["name"], n_persisted, len(alerts))
        _persist_summary(db, summary)
        _report(p, alerts, summary)


if __name__ == "__main__":
    rt   = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    demo = (len(sys.argv) > 2 and sys.argv[2].lower() == "demo")
    run(rt, demo)
