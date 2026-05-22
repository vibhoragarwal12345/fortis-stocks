"""
Factor Overlay
Tags each top-ranked pick with its exposure to the standard equity-style
factors, then aggregates across the portfolio to surface accidental
concentration. A "diversified" list of 30 stocks can secretly all be the
same trade (e.g. growth + momentum + large-cap tech); this agent measures it.

PER-TICKER FACTORS (continuous exposure in [-1, +1], plus binary tags)
  value      P/E vs sector median  ->  cheap -> +1
  growth     YoY revenue / earnings growth  ->  +1 at 30% / -1 at -30%
  quality    composite of ROE, profit margin, debt/equity
  momentum   6-month return vs SPY  ->  +1 if >+20% and beats SPY
  low_vol    30-day realized vol vs SPY's  ->  +1 if lower
  size       market_cap -> large_cap / mid_cap / small_cap
  sector     GICS sector (yfinance .info)

CROSS-PORTFOLIO
  factor_distribution    count of each tag across the analyzed picks
  sector_distribution    same for GICS sector
  concentration_alerts   warnings when any tag/sector/combo exceeds 30-40%
  diversification_score  0..100  (100 = nothing dominates)

Data: yfinance .info (P/E, ratios, growth, ROE, beta, sector, market cap) +
yfinance price history (momentum + realized vol). No LLM involved.

Usage:
    python pipeline/agents/factor_overlay.py [run_type]    # default midday
"""

import logging
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

TOP_N = 30
LARGE_CAP_USD = 10_000_000_000
MID_CAP_USD = 2_000_000_000


def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def _clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


# ══════════════════════════════════════════════════════════════════════════════
# Per-ticker raw metrics
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_info(ticker: str) -> dict:
    """yfinance .info subset used for factor scoring -- never raises."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}
    return {
        "trailing_pe":    _num(info.get("trailingPE")),
        "price_to_book":  _num(info.get("priceToBook")),
        "ev_ebitda":      _num(info.get("enterpriseToEbitda")),
        "revenue_growth": _num(info.get("revenueGrowth")),
        "earnings_growth": _num(info.get("earningsQuarterlyGrowth")
                                or info.get("earningsGrowth")),
        "roe":            _num(info.get("returnOnEquity")),
        "profit_margin":  _num(info.get("profitMargins")),
        "debt_to_equity": _num(info.get("debtToEquity")),
        "market_cap":     _num(info.get("marketCap")),
        "sector":         info.get("sector"),
        "industry":       info.get("industry"),
        "beta":           _num(info.get("beta")),
    }


def _size_class(market_cap):
    if market_cap is None:
        return "unknown"
    if market_cap >= LARGE_CAP_USD:
        return "large_cap"
    if market_cap >= MID_CAP_USD:
        return "mid_cap"
    return "small_cap"


# ══════════════════════════════════════════════════════════════════════════════
# Factor exposures
# ══════════════════════════════════════════════════════════════════════════════

def _value_exposure(pe, sector_median_pe):
    if pe is None or sector_median_pe is None or sector_median_pe <= 0 or pe <= 0:
        return None
    # ratio = pe / sector_median; <1 means cheaper than sector.
    ratio = pe / sector_median_pe
    return round(_clip(1.0 - ratio), 4)


def _growth_exposure(rev_growth, eps_growth):
    # Use whichever is available; if both, average.
    vals = [v for v in (rev_growth, eps_growth) if v is not None]
    if not vals:
        return None
    g = sum(vals) / len(vals)
    return round(_clip(g / 0.30), 4)               # +30% -> +1


def _quality_exposure(roe, profit_margin, debt_to_equity):
    components = []
    if roe is not None:
        components.append(_clip(roe / 0.20))        # 20% ROE -> +1
    if profit_margin is not None:
        components.append(_clip(profit_margin / 0.20))
    if debt_to_equity is not None:
        # yfinance reports D/E as a ratio in percent for many tickers (e.g.
        # 50 = 0.50). Normalize: if >5 it's likely a percent.
        de = debt_to_equity / 100.0 if debt_to_equity > 5 else debt_to_equity
        components.append(_clip(-(de / 1.0) + 0.5))   # D/E=0 -> +0.5, 1.5 -> -1
    if not components:
        return None
    return round(_clip(sum(components) / len(components)), 4)


def _returns_and_vol(prices: pd.Series, n_days: int = 126) -> tuple:
    """6-month total return and 30-day realized annualized vol."""
    if prices is None or len(prices) < 30:
        return None, None
    six_mo = prices.tail(n_days + 1)
    six_mo_ret = (float(six_mo.iloc[-1] / six_mo.iloc[0]) - 1.0
                  if len(six_mo) >= 2 else None)
    daily = prices.tail(31).pct_change().dropna()
    realized_vol = (float(daily.std(ddof=1) * np.sqrt(252))
                    if len(daily) >= 5 else None)
    return six_mo_ret, realized_vol


def _momentum_exposure(ticker_6mo, spy_6mo):
    if ticker_6mo is None or spy_6mo is None:
        return None
    excess = ticker_6mo - spy_6mo
    return round(_clip(excess / 0.20), 4)           # +20% over SPY -> +1


def _low_vol_exposure(ticker_vol, spy_vol):
    if ticker_vol is None or spy_vol is None or ticker_vol <= 0:
        return None
    # 1 - (ticker/SPY) -- positive when ticker is calmer than SPY.
    return round(_clip(1.0 - ticker_vol / spy_vol), 4)


# ══════════════════════════════════════════════════════════════════════════════
# Tag derivation
# ══════════════════════════════════════════════════════════════════════════════

def _derive_tags(metrics: dict, exposures: dict) -> list[str]:
    tags = []
    if exposures.get("value_exposure") is not None and exposures["value_exposure"] > 0.30:
        tags.append("value")
    rev = metrics.get("revenue_growth") or 0
    eps = metrics.get("earnings_growth") or 0
    if rev > 0.20 or eps > 0.25:
        tags.append("growth")
    roe = metrics.get("roe") or 0
    de = metrics.get("debt_to_equity") or 0
    de = de / 100.0 if de > 5 else de
    if roe > 0.15 and de < 0.5:
        tags.append("quality")
    if exposures.get("momentum_exposure") is not None and exposures["momentum_exposure"] > 0:
        if metrics.get("ticker_6mo") and metrics["ticker_6mo"] > 0.20:
            tags.append("momentum")
    if exposures.get("low_vol_exposure") is not None and exposures["low_vol_exposure"] > 0:
        tags.append("low_vol")
    tags.append(metrics["size_classification"])
    return tags


# ══════════════════════════════════════════════════════════════════════════════
# Cross-portfolio analysis
# ══════════════════════════════════════════════════════════════════════════════

def _cross_portfolio(rows: list[dict]) -> dict:
    n = len(rows)
    factor_dist = Counter()
    sector_dist = Counter()
    for r in rows:
        for tag in (r.get("factor_tags") or []):
            factor_dist[tag] += 1
        if r.get("sector"):
            sector_dist[r["sector"]] += 1

    # Concentration alerts
    alerts = []
    for tag, count in factor_dist.items():
        share = count / n
        if share >= 0.40:
            alerts.append(f"{count}/{n} picks ({share*100:.0f}%) tagged '{tag}' "
                          f"-- concentrated single-factor exposure")
    for sector, count in sector_dist.items():
        share = count / n
        if share >= 0.30:
            alerts.append(f"{count}/{n} picks ({share*100:.0f}%) in {sector} "
                          f"-- sector overweight")

    # Composite-tag alert: growth + momentum overlap
    gm = sum(1 for r in rows if set(r.get("factor_tags") or [])
             >= {"growth", "momentum"})
    if gm / n >= 0.30:
        alerts.append(f"{gm}/{n} picks ({gm/n*100:.0f}%) are BOTH growth AND "
                      f"momentum -- vulnerable to rate-up / risk-off shocks")

    # Diversification score: 100 - max(max_tag_share, max_sector_share) * 100.
    max_tag_share = (max(factor_dist.values()) / n) if factor_dist else 0.0
    max_sec_share = (max(sector_dist.values()) / n) if sector_dist else 0.0
    div_score = round(max(0.0, 100.0 - max(max_tag_share, max_sec_share) * 100.0), 1)

    return {"factor_distribution": dict(factor_dist),
            "sector_distribution": dict(sector_dist),
            "concentration_alerts": alerts,
            "diversification_score": div_score}


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday") -> None:
    if run_type not in ("premarket", "midday", "close"):
        run_type = "midday"
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    recent = (db.table("ranked_focus_list").select("run_date")
              .order("run_date", desc=True).limit(1).execute().data)
    if not recent:
        log.warning("ranked_focus_list empty -- run ranking_engine first.")
        return
    run_date = recent[0]["run_date"]
    picks = (db.table("ranked_focus_list").select("ticker,rank")
             .eq("run_date", run_date).eq("run_type", run_type)
             .order("rank").limit(TOP_N).execute().data or [])
    tickers = [p["ticker"] for p in picks]
    if not tickers:
        return
    log.info("Factor overlay -- %d tickers from %s/%s", len(tickers), run_date, run_type)

    # ── Price data (top 30 + SPY) ─────────────────────────────────────────────
    raw = yf.download(tickers + ["SPY"], period="1y", interval="1d",
                      auto_adjust=True, progress=False, group_by="ticker")
    multi = isinstance(raw.columns, pd.MultiIndex)
    prices = {}
    for t in tickers + ["SPY"]:
        try:
            prices[t] = (raw[t]["Close"] if multi else raw["Close"]).dropna()
        except (KeyError, TypeError):
            pass
    spy_6mo, spy_vol = _returns_and_vol(prices.get("SPY"))

    # ── Per-ticker .info -- throttled (yfinance hates rapid fire) ─────────────
    metrics_by = {}
    for t in tickers:
        m = _fetch_info(t)
        ticker_6mo, ticker_vol = _returns_and_vol(prices.get(t))
        m["ticker_6mo"], m["ticker_vol"] = ticker_6mo, ticker_vol
        m["size_classification"] = _size_class(m["market_cap"])
        metrics_by[t] = m
        time.sleep(0.15)
    log.info("Fetched .info + price-returns for %d tickers", len(metrics_by))

    # ── Sector median P/E -- compute after collecting all info ────────────────
    by_sector = {}
    for t, m in metrics_by.items():
        sec, pe = m.get("sector"), m.get("trailing_pe")
        if sec and pe and pe > 0:
            by_sector.setdefault(sec, []).append(pe)
    sector_median_pe = {s: statistics.median(v)
                        for s, v in by_sector.items() if len(v) >= 3}

    # ── Per-ticker factor exposures + tags ────────────────────────────────────
    today = datetime.now(timezone.utc).date().isoformat()
    db_rows = []
    for t, m in metrics_by.items():
        exposures = {
            "value_exposure":    _value_exposure(m.get("trailing_pe"),
                                                  sector_median_pe.get(m.get("sector"))),
            "growth_exposure":   _growth_exposure(m.get("revenue_growth"),
                                                   m.get("earnings_growth")),
            "quality_exposure":  _quality_exposure(m.get("roe"),
                                                    m.get("profit_margin"),
                                                    m.get("debt_to_equity")),
            "momentum_exposure": _momentum_exposure(m.get("ticker_6mo"), spy_6mo),
            "low_vol_exposure":  _low_vol_exposure(m.get("ticker_vol"), spy_vol),
        }
        tags = _derive_tags(m, exposures)
        db_rows.append({
            "ticker": t, "snapshot_date": today,
            **exposures,
            "size_classification": m["size_classification"],
            "sector": m.get("sector"),
            "factor_tags": tags,
            "raw_metrics": {k: round(v, 4) if isinstance(v, float) else v
                             for k, v in m.items()
                             if k not in ("ticker_vol", "ticker_6mo")},
        })

    cross = _cross_portfolio(db_rows)

    # ── Persist ───────────────────────────────────────────────────────────────
    try:
        db.table("factor_exposure").upsert(
            db_rows, on_conflict="ticker,snapshot_date").execute()
        log.info("factor_exposure: %d rows persisted", len(db_rows))
    except Exception as exc:
        log.warning("factor_exposure not persisted (%s) -- apply migration 022",
                    getattr(exc, "code", exc))
    try:
        db.table("factor_concentration").upsert({
            "run_date": run_date, "run_type": run_type,
            "factor_distribution": cross["factor_distribution"],
            "sector_distribution": cross["sector_distribution"],
            "concentration_alerts": cross["concentration_alerts"],
            "diversification_score": cross["diversification_score"],
            "picks_analyzed": len(db_rows),
        }, on_conflict="run_date,run_type").execute()
        log.info("factor_concentration: 1 row persisted")
    except Exception as exc:
        log.warning("factor_concentration not persisted (%s)",
                    getattr(exc, "code", exc))

    _report(db_rows, cross)


def _report(rows: list[dict], cross: dict) -> None:
    bar = "=" * 76
    print(f"\n{bar}\nFACTOR OVERLAY -- {len(rows)} picks\n{bar}")

    print("\n[ FACTOR-TAG DISTRIBUTION ]")
    for tag, n in sorted(cross["factor_distribution"].items(),
                         key=lambda kv: -kv[1]):
        bar_len = int(n / len(rows) * 30)
        print(f"  {tag:<14}{n:>4}  {'#' * bar_len}")

    print("\n[ SECTOR DISTRIBUTION ]")
    for sector, n in sorted(cross["sector_distribution"].items(),
                            key=lambda kv: -kv[1]):
        share = n / len(rows) * 100
        print(f"  {sector or 'Unknown':<28}{n:>4}  ({share:.0f}%)")

    print(f"\n[ DIVERSIFICATION SCORE ] {cross['diversification_score']}/100")

    if cross["concentration_alerts"]:
        print(f"\n[ CONCENTRATION ALERTS -- {len(cross['concentration_alerts'])} ]")
        for a in cross["concentration_alerts"]:
            print(f"  !  {a}")
    else:
        print("\n[ CONCENTRATION ALERTS ]  (none -- portfolio reasonably diversified)")

    print(f"\n[ TOP TAGGED PICKS -- first 10 ]")
    for r in rows[:10]:
        tags = ", ".join(r["factor_tags"]) or "(no tags)"
        sec = (r.get("sector") or "")[:24]
        print(f"  {r['ticker']:<8}{sec:<26}  tags: {tags}")
    print(bar + "\n")


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
