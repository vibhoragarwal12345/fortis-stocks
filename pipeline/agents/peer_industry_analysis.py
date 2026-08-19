"""
Peer Industry Analysis
The 3D verdict for every A-grade pick: is this industry investable, is this
the right name in the industry, and why this name over its closest peer.

  PART A  Quantitative peer comparison
          Twelve metrics (valuation / profitability / growth / quality) for
          target + each peer, percentile ranks, key deltas (top + bottom
          quartile), weighted overall_peer_score, within-industry ranking,
          best_in_industry.

  PART B  Industry health
          Industry revenue growth, margin trend, valuation vs 5y avg, beta,
          relative strength, cycle position (early/mid/late/contraction/
          recovery), disruption risk (heuristic), composite investability
          score (0-100), industry_stance (overweight/market_weight/
          underweight).

  PART C  Narrative
          Groq closed-context generation with mandatory [DATA REF: key]
          discipline. Six named sections. Output passes through
          factcheck_agent -- verification_score & quality_grade persisted.

All numeric inputs come from deterministic API calls (yfinance .info,
Finnhub /stock/metric, FMP /v3/income-statement). The LLM only synthesizes;
it does NOT introduce numbers.

Writes peer_industry_analysis (migration 027) and, when the best-in-industry
peer beats the target by > 15 points, appends a row to auto_added_watchlist
(migration 028).

Usage:
    python pipeline/agents/peer_industry_analysis.py [run_type]
"""

import json
import logging
import math
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (  # noqa: E402
    FINNHUB_API_KEY, FMP_API_KEY, GROQ_API_KEY,
    SUPABASE_SERVICE_KEY, SUPABASE_URL,
)
from supabase import create_client  # noqa: E402
from agents.factcheck_agent import factcheck, strip_data_refs  # noqa: E402
from agents import industry_research as ind_res  # noqa: E402

HTTP_TIMEOUT       = 25
GROQ_MODEL         = "openai/gpt-oss-120b"
FINNHUB_THROTTLE   = 1.1
FMP_THROTTLE       = 0.3

# Industry-aggregate computation: how many same-sector tickers to sample.
INDUSTRY_PEER_LIMIT = 30

# Spec weights for the composite peer score.
W_VALUATION    = 0.25
W_PROFITABILITY = 0.30
W_GROWTH       = 0.25
W_QUALITY      = 0.20

# Adjustment threshold for auto-added watchlist.
WATCHLIST_DELTA_THRESHOLD = 15

# Sector -> SPDR ETF for relative-strength comparison.
SECTOR_ETF = {
    "Technology": "XLK", "Communication Services": "XLC",
    "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
    "Energy": "XLE", "Financial Services": "XLF",
    "Healthcare": "XLV", "Industrials": "XLI",
    "Basic Materials": "XLB", "Real Estate": "XLRE",
    "Utilities": "XLU",
}

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


def _safe_median(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return statistics.median(xs) if xs else None


def _percentile_rank(xs: list[float | None], target_val: float | None,
                     higher_is_better: bool) -> float | None:
    """Return target's percentile rank within xs (0..1). Larger value = better
    rank when higher_is_better, else inverted."""
    if target_val is None:
        return None
    vals = [v for v in xs if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return None
    if higher_is_better:
        n_worse = sum(1 for v in vals if v < target_val)
    else:
        n_worse = sum(1 for v in vals if v > target_val)
    return n_worse / len(vals)


# ══════════════════════════════════════════════════════════════════════════════
# Per-ticker metric fetching
# ══════════════════════════════════════════════════════════════════════════════

class _InfoCache:
    def __init__(self):
        self._d: dict[str, dict] = {}

    def get(self, ticker: str) -> dict:
        if ticker in self._d:
            return self._d[ticker]
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception:
            info = {}
        self._d[ticker] = info
        return info


def _finnhub_metric(ticker: str) -> dict:
    """GET /stock/metric -- all profitability, growth, valuation keys."""
    if not FINNHUB_API_KEY:
        return {}
    try:
        resp = requests.get("https://finnhub.io/api/v1/stock/metric",
                            params={"symbol": ticker, "metric": "all",
                                    "token": FINNHUB_API_KEY},
                            timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return {}
        data = resp.json() or {}
        out = data.get("metric") or {}
        out["_series"] = data.get("series") or {}
        return out
    except Exception:
        return {}
    finally:
        time.sleep(FINNHUB_THROTTLE)


def _fmp_income_quarterly(ticker: str, limit: int = 8) -> list[dict]:
    """GET /v3/income-statement?period=quarter -- for gross margin trend."""
    if not FMP_API_KEY:
        return []
    try:
        resp = requests.get(
            f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}",
            params={"period": "quarter", "limit": limit, "apikey": FMP_API_KEY},
            timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return []
        return resp.json() or []
    except Exception:
        return []
    finally:
        time.sleep(FMP_THROTTLE)


def _gross_margin_trend(income_rows: list[dict]) -> tuple[str, float | None]:
    """Classify gross margin slope as improving / stable / declining over 8q."""
    if len(income_rows) < 4:
        return "unknown", None
    pts = []
    for row in income_rows:
        rev = _num(row.get("revenue"))
        gp  = _num(row.get("grossProfit"))
        if rev and gp is not None and rev > 0:
            pts.append(gp / rev)
    if len(pts) < 4:
        return "unknown", None
    # API returns newest-first; reverse to oldest-first for slope.
    pts = list(reversed(pts))
    x = np.arange(len(pts))
    slope, _ = np.polyfit(x, pts, 1)
    if slope > 0.005:
        label = "improving"
    elif slope < -0.005:
        label = "declining"
    else:
        label = "stable"
    return label, float(slope)


def _gather_metrics(ticker: str, info_cache: _InfoCache) -> dict:
    """All 12 metrics for one ticker."""
    info  = info_cache.get(ticker)
    metric = _finnhub_metric(ticker)
    income = _fmp_income_quarterly(ticker, limit=8)
    margin_label, margin_slope = _gross_margin_trend(income)

    pe       = _num(info.get("trailingPE"))
    ev_ebitda = _num(info.get("enterpriseToEbitda"))
    ps       = _num(info.get("priceToSalesTrailing12Months"))
    peg      = _num(info.get("trailingPegRatio") or info.get("pegRatio"))
    de       = _num(info.get("debtToEquity"))
    if de is not None and de > 5:        # yfinance sometimes reports DE as %
        de = de / 100.0

    # NOTE: Finnhub /stock/metric returns these in PERCENT units
    # (e.g. 11.5 means 11.5%, NOT 0.115). We normalize them to DECIMAL form
    # here so downstream code can treat every margin/growth field uniformly.
    def _pct_to_decimal(x):
        return None if x is None else x / 100.0

    op_margin = _pct_to_decimal(_num(metric.get("operatingMarginTTM"))
                                or _num(metric.get("operatingMarginAnnual")))
    roe       = _pct_to_decimal(_num(metric.get("roeTTM"))
                                or _num(metric.get("roeRfy")))
    roic      = _pct_to_decimal(_num(metric.get("roiTTM"))
                                or _num(metric.get("roicTTM"))
                                or _num(metric.get("roi5Y")))
    rev_growth = _pct_to_decimal(_num(metric.get("revenueGrowthTTMYoy"))
                                  or _num(metric.get("revenueGrowth5Y")))
    eps_growth_raw = _pct_to_decimal(_num(metric.get("epsGrowthTTMYoy"))
                                      or _num(metric.get("epsGrowth5Y")))
    # EPS growth has a base-effect problem: a company turning from -$0.01 to
    # +$0.01 EPS shows up as a "+200% YoY" number that is mathematically
    # correct but distorts every peer-median calculation. Clamp to +/- 200%
    # for ranking/median purposes but preserve the raw value in a sidecar
    # field so the narrative can mention the actual figure if it matters.
    if eps_growth_raw is not None and abs(eps_growth_raw) > 2.0:
        eps_growth = 2.0 if eps_growth_raw > 0 else -2.0
    else:
        eps_growth = eps_growth_raw
    # FCF margin: computed from absolute dollars (decimal already), or
    # fcfMarginAnnual which Finnhub gives in percent -- normalize.
    fcf_margin = _num(metric.get("freeCashFlowAnnual"))
    rev_annual = _num(metric.get("revenueAnnual"))
    if fcf_margin is not None and rev_annual:
        fcf_margin = fcf_margin / rev_annual                # already decimal
    else:
        fcf_margin = _pct_to_decimal(_num(metric.get("fcfMarginAnnual")))

    return {
        "pe":                pe,
        "ev_ebitda":         ev_ebitda,
        "ps":                ps,
        "peg":               peg,
        "operating_margin":  op_margin,
        "roe":               roe,
        "roic":              roic,
        "gross_margin_trend": margin_label,
        "gross_margin_slope": margin_slope,
        "revenue_growth":    rev_growth,
        "eps_growth":        eps_growth,
        "debt_equity":       de,
        "fcf_margin":        fcf_margin,
        "_market_cap":       _num(info.get("marketCap")),
        "_sector":           info.get("sector"),
        "_industry":         info.get("industry"),
        "_long_summary":     info.get("longBusinessSummary"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Metric definitions -- weight and direction
# ══════════════════════════════════════════════════════════════════════════════

METRICS = [
    ("pe",               "Valuation",     False),  # lower P/E = better
    ("ev_ebitda",        "Valuation",     False),
    ("ps",               "Valuation",     False),
    ("peg",              "Valuation",     False),
    ("operating_margin", "Profitability", True),
    ("roe",              "Profitability", True),
    ("roic",             "Profitability", True),
    ("gross_margin_slope", "Profitability", True),  # margin trend slope
    ("revenue_growth",   "Growth",        True),
    ("eps_growth",       "Growth",        True),
    ("debt_equity",      "Quality",       False),  # lower = better
    ("fcf_margin",       "Quality",       True),
]

CATEGORY_WEIGHTS = {
    "Valuation":     W_VALUATION,
    "Profitability": W_PROFITABILITY,
    "Growth":        W_GROWTH,
    "Quality":       W_QUALITY,
}


# ══════════════════════════════════════════════════════════════════════════════
# Part A: peer comparison
# ══════════════════════════════════════════════════════════════════════════════

def _compute_part_a(target: str, peers: list[str], info_cache: _InfoCache):
    log.info("  Part A: fetching metrics for %d ticker(s)", 1 + len(peers))
    by_ticker = {target: _gather_metrics(target, info_cache)}
    for p in peers:
        by_ticker[p] = _gather_metrics(p, info_cache)

    universe = [target] + peers

    # Per-metric percentile rank within universe (target + peers).
    ranks: dict[str, dict[str, float | None]] = {t: {} for t in universe}
    cat_scores: dict[str, dict[str, list[float]]] = {
        t: defaultdict(list) for t in universe
    }
    medians: dict[str, float | None] = {}

    for metric_key, category, higher_better in METRICS:
        vals = [by_ticker[t].get(metric_key) for t in universe]
        medians[metric_key] = _safe_median(vals[1:])  # peer median (exclude target)
        for t in universe:
            v = by_ticker[t].get(metric_key)
            pr = _percentile_rank(vals, v, higher_better)
            ranks[t][metric_key] = pr
            if pr is not None:
                cat_scores[t][category].append(pr)

    # Composite peer score: weighted avg of category averages.
    overall: dict[str, float] = {}
    for t in universe:
        total_weight = 0.0
        weighted_sum = 0.0
        for cat, w in CATEGORY_WEIGHTS.items():
            if cat_scores[t].get(cat):
                cat_avg = sum(cat_scores[t][cat]) / len(cat_scores[t][cat])
                weighted_sum += cat_avg * w
                total_weight += w
        overall[t] = (weighted_sum / total_weight * 100) if total_weight else 0.0

    ranked = sorted(universe, key=lambda t: -overall[t])
    target_rank = ranked.index(target) + 1
    best = ranked[0]

    # Key deltas: target metrics in top or bottom quartile.
    # delta_pct = (target - peer_median) / |peer_median| * 100. Two pathologies:
    #   * small denominators ("PE 5.97x peer median" gets reported as 597%)
    #   * extreme target values vs near-zero median (EPS growth 200% vs 2.8%
    #     median -> 7000%+ noise that dominates narratives).
    # Defense: floor the denominator AND clamp the result. Above the clamp
    # we drop the % (set None) -- rank/percentile already conveys the signal.
    DELTA_DENOM_FLOOR = 0.05    # 5% -- below this, denominator is too noisy
    DELTA_CLAMP_PCT   = 500     # %  -- above this, the number is meaningless
    target_metrics = by_ticker[target]
    key_deltas = []
    for metric_key, category, higher_better in METRICS:
        pr = ranks[target].get(metric_key)
        if pr is None:
            continue
        if pr >= 0.75 or pr <= 0.25:
            tv = target_metrics.get(metric_key)
            pm = medians[metric_key]
            if tv is None or pm is None:
                continue
            if abs(pm) < DELTA_DENOM_FLOOR:
                delta_pct = None
            else:
                d = (tv - pm) / abs(pm) * 100
                if abs(d) > DELTA_CLAMP_PCT:
                    delta_pct = None
                else:
                    delta_pct = round(d, 1)
            key_deltas.append({
                "metric":       metric_key,
                "category":     category,
                "target_value": round(tv, 4) if isinstance(tv, (int, float)) else tv,
                "peer_median":  round(pm, 4) if isinstance(pm, (int, float)) else pm,
                "percentile":   round(pr, 3),
                "rank_label":   "top_quartile" if pr >= 0.75 else "bottom_quartile",
                "delta_pct":    delta_pct,
            })

    return {
        "by_ticker":     by_ticker,
        "ranks":         ranks,
        "medians":       medians,
        "overall":       {t: round(v, 2) for t, v in overall.items()},
        "ranked":        ranked,
        "target_rank":   target_rank,
        "best":          best,
        "key_deltas":    key_deltas,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Part B: industry health
# ══════════════════════════════════════════════════════════════════════════════

def _industry_relative_strength(target_sector: str) -> tuple[float | None, str | None]:
    """ETF performance vs SPY over 120d, returned as relative-strength score
    (0..1) for the investability formula. Also returns the ETF symbol."""
    etf = SECTOR_ETF.get(target_sector)
    if not etf:
        return None, None
    try:
        raw = yf.download([etf, "SPY"], period="1y", interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker",
                          threads=False)
    except Exception:
        return None, etf
    multi = isinstance(raw.columns, pd.MultiIndex)
    try:
        etf_s = (raw[etf]["Close"] if multi else raw["Close"]).dropna()
        spy_s = (raw["SPY"]["Close"] if multi else raw["Close"]).dropna()
    except Exception:
        return None, etf
    if len(etf_s) < 120 or len(spy_s) < 120:
        return None, etf
    etf_120 = (etf_s.iloc[-1] / etf_s.iloc[-120]) - 1
    spy_120 = (spy_s.iloc[-1] / spy_s.iloc[-120]) - 1
    excess = float(etf_120 - spy_120)
    # Map [-0.15, +0.15] -> [0, 1]; clip outside.
    score = max(0.0, min(1.0, (excess + 0.15) / 0.30))
    return score, etf


def _industry_beta(target_sector: str) -> float | None:
    etf = SECTOR_ETF.get(target_sector)
    if not etf:
        return None
    try:
        raw = yf.download([etf, "SPY"], period="1y", interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker",
                          threads=False)
    except Exception:
        return None
    multi = isinstance(raw.columns, pd.MultiIndex)
    try:
        e = (raw[etf]["Close"] if multi else raw["Close"]).dropna().pct_change().dropna()
        s = (raw["SPY"]["Close"] if multi else raw["Close"]).dropna().pct_change().dropna()
    except Exception:
        return None
    df = pd.concat([e, s], axis=1, join="inner").tail(252)
    if len(df) < 60:
        return None
    cov = df.iloc[:, 0].cov(df.iloc[:, 1])
    var = df.iloc[:, 1].var()
    if not var:
        return None
    return float(cov / var)


def _cycle_position(industry_metrics: dict) -> tuple[str, float]:
    """Heuristic classifier. Returns (label, cycle_score_for_investability)."""
    rev_growth = _num(industry_metrics.get("median_revenue_growth")) or 0
    margin_label = industry_metrics.get("median_margin_trend") or "unknown"
    val_premium = _num(industry_metrics.get("valuation_premium")) or 0  # > 0 = expensive
    if rev_growth > 0.10 and margin_label == "improving":
        return "early_cycle", 0.85
    if rev_growth > 0.04 and margin_label in ("stable", "improving") and abs(val_premium) < 0.10:
        return "mid_cycle", 0.55
    if rev_growth < 0.0 and margin_label == "improving":
        return "recovery", 0.95
    if rev_growth < 0.0 and margin_label != "improving":
        return "contraction", 0.30
    if rev_growth >= 0 and val_premium > 0.15 and margin_label != "improving":
        return "late_cycle", 0.25
    return "mid_cycle", 0.50


def _compute_part_b(target_sector: str, target_industry: str,
                    by_ticker: dict, peers: list[str]) -> dict:
    """Industry aggregates computed from the peer set itself + the sector ETF."""
    log.info("  Part B: industry aggregates -- sector=%s", target_sector)

    rev_growths = [by_ticker[p].get("revenue_growth") for p in peers
                   if by_ticker[p].get("revenue_growth") is not None]
    op_margins  = [by_ticker[p].get("operating_margin") for p in peers
                   if by_ticker[p].get("operating_margin") is not None]
    margin_trends = [by_ticker[p].get("gross_margin_trend") for p in peers
                     if by_ticker[p].get("gross_margin_trend") not in (None, "unknown")]
    pes = [by_ticker[p].get("pe") for p in peers
           if by_ticker[p].get("pe") is not None and by_ticker[p].get("pe") > 0]
    margin_slopes = [by_ticker[p].get("gross_margin_slope") for p in peers
                     if by_ticker[p].get("gross_margin_slope") is not None]

    median_rev_growth = _safe_median(rev_growths)
    median_op_margin  = _safe_median(op_margins)
    median_pe         = _safe_median(pes)
    median_margin_slope = _safe_median(margin_slopes)

    # Aggregated margin trend label: pick the most common.
    if margin_trends:
        from collections import Counter
        median_margin_trend = Counter(margin_trends).most_common(1)[0][0]
    else:
        median_margin_trend = "unknown"

    # 5-year average forward P/E proxy: industry forward P/E vs current median.
    # Without a proper 5y history, we use the current PE distribution itself
    # as the "fair value" anchor and flag relative dispersion. valuation_premium
    # near 0 means "at-history".
    valuation_premium = 0.0
    if pes and len(pes) >= 4:
        # If the median PE is more than 1.3x the 25th percentile, the industry
        # is trading rich vs its own cheaper names -- a coarse proxy.
        pes_sorted = sorted(pes)
        p25 = pes_sorted[max(0, len(pes_sorted) // 4)]
        if p25 > 0 and median_pe:
            valuation_premium = (median_pe - p25) / p25
            valuation_premium = max(-0.5, min(0.5, valuation_premium))

    rs_score, etf = _industry_relative_strength(target_sector)
    beta = _industry_beta(target_sector)

    # ── Step 6.5 backfills via industry_research module ─────────────────────
    # 5-year P/E approximation overrides the dispersion proxy when peers
    # produce usable history.
    log.info("  Part B: SEC EDGAR disruption scan + 5y P/E + M&A + IPO...")
    pe5 = ind_res.approx_industry_5y_pe(peers)
    if pe5.get("premium_ratio") is not None:
        valuation_premium = max(-0.5, min(0.5, float(pe5["premium_ratio"])))
        log.info("    5y P/E: current=%.2f, 5y_avg=%.2f, premium_ratio=%+.2f (n=%d)",
                 pe5.get("current_median_pe") or 0,
                 pe5.get("fiveyr_avg_pe") or 0,
                 pe5.get("premium_ratio"),
                 pe5.get("n_covered") or 0)

    # SEC EDGAR disruption scan (replaces the 0.25 baseline).
    disruption = ind_res.disruption_risk_from_edgar(target_industry)
    disruption_risk_raw = disruption.get("risk_score")
    if disruption_risk_raw is not None:
        disruption_risk = disruption_risk_raw / 100.0
        log.info("    disruption (EDGAR): %s/100  (%s recent / %s prior 10-K hits)",
                 disruption_risk_raw, disruption.get("recent_hits"),
                 disruption.get("prior_hits"))
    else:
        disruption_risk = 0.25
        log.info("    disruption (EDGAR): unavailable, using baseline 25/100")

    # M&A + IPO activity.
    ma = ind_res.ma_activity(peers, industry=target_industry)
    ipo = ind_res.ipo_activity(target_industry, window_months=12)
    log.info("    M&A: finnhub=%s, edgar_8k=%s, score=%s",
             ma.get("finnhub_mentions"), ma.get("edgar_8k_count"), ma.get("score"))
    log.info("    IPO: total=%s, industry_matches=%s, score=%s",
             ipo.get("total_ipos"), ipo.get("industry_matches"), ipo.get("score"))

    industry_metrics = {
        "median_revenue_growth": median_rev_growth,
        "median_operating_margin": median_op_margin,
        "median_pe":             median_pe,
        "median_margin_trend":   median_margin_trend,
        "median_margin_slope":   median_margin_slope,
        "valuation_premium":     valuation_premium,
        "sector_etf":            etf,
        "etf_relative_strength_120d": rs_score,
        "industry_beta_vs_spy":  beta,
        "ma_activity_score":     ma.get("score"),
        "ma_finnhub_mentions":   ma.get("finnhub_mentions"),
        "ma_edgar_8k_count":     ma.get("edgar_8k_count"),
        "ipo_activity_score":    ipo.get("score"),
        "ipo_industry_matches":  ipo.get("industry_matches"),
        "ipo_total":             ipo.get("total_ipos"),
        "fiveyr_avg_pe":         pe5.get("fiveyr_avg_pe"),
        "current_median_pe_5y_basis": pe5.get("current_median_pe"),
        "fiveyr_pe_n_covered":   pe5.get("n_covered"),
        "disruption_recent_hits": disruption.get("recent_hits"),
        "disruption_prior_hits": disruption.get("prior_hits"),
        "disruption_industry_keyword": disruption.get("industry_keyword"),
    }
    cycle_label, cycle_score = _cycle_position(industry_metrics)
    industry_metrics["cycle_position"] = cycle_label

    # Composite investability score per spec weights.
    growth_score   = 0.5 if median_rev_growth is None else \
                     max(0.0, min(1.0, median_rev_growth * 3 + 0.5))
    margin_trend_score = ({"improving": 1.0, "stable": 0.6,
                           "declining": 0.2, "unknown": 0.5}[median_margin_trend])
    valuation_premium_score = max(0.0, min(1.0, 0.5 + valuation_premium))
    rs_score_final = rs_score if rs_score is not None else 0.5

    investability = (
        0.25 * growth_score
      + 0.20 * margin_trend_score
      + 0.15 * (1.0 - valuation_premium_score)
      + 0.15 * rs_score_final
      + 0.15 * (1.0 - disruption_risk)
      + 0.10 * cycle_score
    )
    investability_score = round(investability * 100, 1)

    if investability_score > 70:
        stance = "overweight"
    elif investability_score >= 40:
        stance = "market_weight"
    else:
        stance = "underweight"

    return {
        "industry_metrics":     industry_metrics,
        "industry_cycle_position": cycle_label,
        "investability_score":  investability_score,
        "industry_stance":      stance,
        "disruption_risk_score": round(disruption_risk * 100, 1),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Part C: narrative
# ══════════════════════════════════════════════════════════════════════════════

_NARRATIVE_SYSTEM = (
    "You are a senior equity analyst writing peer-comparison commentary.\n\n"
    "STRICT RULES:\n"
    "1) You may ONLY cite numbers from the DATA section below.\n"
    "2) EVERY single number you write -- percentages, ratios, dollar amounts, "
    "scores, ranks -- MUST be IMMEDIATELY followed by a [DATA REF: key_name] "
    "tag where key_name is the EXACT key from the DATA section.\n"
    "3) Copy the value VERBATIM from the DATA section. Do NOT round, "
    "re-format, or change the sign. If the DATA says '-74.5%', write "
    "'-74.5%', not '74.5%'.\n"
    "4) Do not invent keys. If a key isn't in the DATA section, do not cite.\n"
    "5) Do not introduce facts or numbers from training memory.\n"
    "6) Stay concise. Better short and verified than long and rejected.\n\n"
    "GOOD examples:\n"
    "  CORRECT: 'target trades at 25.5x P/E [DATA REF: target_pe] vs peer "
    "median 31.2x [DATA REF: peer_median_pe]'\n"
    "  CORRECT: 'ROE of 22.9% [DATA REF: target_roe] beats the peer median "
    "12.0% [DATA REF: peer_median_roe]'\n\n"
    "BAD examples (will fail verification):\n"
    "  WRONG: 'high P/E ratio of 28.6' -- missing [DATA REF] tag\n"
    "  WRONG: 'operating margin of 22.9%' followed by '[DATA REF: "
    "peer_median_operating_margin]' -- this is the TARGET's value but cited "
    "as the peer median. Use [DATA REF: target_operating_margin] instead.\n"
    "  WRONG: 'debt ratio of 74.5%' when DATA says '-74.5%' -- you dropped "
    "the sign. Write '-74.5%'.\n"
    "  WRONG: '[DATA REF: roic]' -- the key is 'target_roic', not 'roic'.\n\n"
    "Always use the colon: [DATA REF: key_name], never [DATA REF key_name]."
)


def _build_data_dict(target: str, part_a: dict, part_b: dict) -> dict:
    """Flat key/value bag the LLM may cite via [DATA REF: key]."""
    by = part_a["by_ticker"]
    tm = by[target]
    medians = part_a["medians"]
    overall = part_a["overall"]
    industry = part_b["industry_metrics"]

    def fmt(v, kind=None):
        if v is None:
            return "n/a"
        if kind == "pct":
            return f"{v*100:.1f}%" if isinstance(v, (int, float)) else str(v)
        if isinstance(v, float):
            return f"{v:.2f}"
        return str(v)

    d = {
        "target_ticker":               target,
        "target_pe":                   fmt(tm["pe"]),
        "target_ev_ebitda":            fmt(tm["ev_ebitda"]),
        "target_ps":                   fmt(tm["ps"]),
        "target_peg":                  fmt(tm["peg"]),
        "target_operating_margin":     fmt(tm["operating_margin"], "pct"),
        "target_roe":                  fmt(tm["roe"], "pct"),
        "target_roic":                 fmt(tm["roic"], "pct"),
        "target_gross_margin_trend":   fmt(tm["gross_margin_trend"]),
        "target_revenue_growth":       fmt(tm["revenue_growth"], "pct"),
        "target_eps_growth":           fmt(tm["eps_growth"], "pct"),
        "target_debt_equity":          fmt(tm["debt_equity"]),
        "target_fcf_margin":           fmt(tm["fcf_margin"], "pct"),
        "peer_median_pe":              fmt(medians.get("pe")),
        "peer_median_ev_ebitda":       fmt(medians.get("ev_ebitda")),
        "peer_median_ps":              fmt(medians.get("ps")),
        "peer_median_operating_margin": fmt(medians.get("operating_margin"), "pct"),
        "peer_median_roe":             fmt(medians.get("roe"), "pct"),
        "peer_median_roic":            fmt(medians.get("roic"), "pct"),
        "peer_median_revenue_growth":  fmt(medians.get("revenue_growth"), "pct"),
        "peer_median_eps_growth":      fmt(medians.get("eps_growth"), "pct"),
        "peer_median_debt_equity":     fmt(medians.get("debt_equity")),
        "peer_median_fcf_margin":      fmt(medians.get("fcf_margin"), "pct"),
        "overall_peer_score":          fmt(overall[target]),
        "within_industry_rank":        f"{part_a['target_rank']}/{len(part_a['ranked'])}",
        "best_in_industry_ticker":     part_a["best"],
        "best_in_industry_score":      fmt(overall[part_a["best"]]),
        "industry_stance":             part_b["industry_stance"],
        "industry_cycle_position":     part_b["industry_cycle_position"],
        "investability_score":         fmt(part_b["investability_score"]),
        # disruption_risk_score is already on a 0-100 scale (NOT a decimal),
        # so format as a raw number with /100 unit context -- never *100.
        "disruption_risk_score":       fmt(part_b["disruption_risk_score"]),
        "industry_median_revenue_growth": fmt(industry.get("median_revenue_growth"), "pct"),
        "industry_median_pe":          fmt(industry.get("median_pe")),
        "industry_median_margin_trend": fmt(industry.get("median_margin_trend")),
        "sector_etf":                  fmt(industry.get("sector_etf")),
        "industry_etf_relative_strength_120d":
            fmt(industry.get("etf_relative_strength_120d")),
        "industry_beta_vs_spy":        fmt(industry.get("industry_beta_vs_spy")),
        "industry_valuation_premium":  fmt(industry.get("valuation_premium")),
        # New Step 6.5 backfill data points exposed to the narrative LLM:
        "industry_fiveyr_avg_pe":      fmt(industry.get("fiveyr_avg_pe")),
        "industry_fiveyr_pe_coverage": fmt(industry.get("fiveyr_pe_n_covered")),
        "industry_ma_score":           fmt(industry.get("ma_activity_score")),
        "industry_ma_edgar_8k_count":  fmt(industry.get("ma_edgar_8k_count")),
        "industry_ma_finnhub_mentions": fmt(industry.get("ma_finnhub_mentions")),
        "industry_ipo_score":          fmt(industry.get("ipo_activity_score")),
        "industry_ipo_count":          fmt(industry.get("ipo_industry_matches")),
        "industry_disruption_recent_10k_hits": fmt(industry.get("disruption_recent_hits")),
        "industry_disruption_prior_10k_hits":  fmt(industry.get("disruption_prior_hits")),
    }
    # Also expose each peer's overall score, e.g. peer_BL_score=...
    for t, sc in overall.items():
        d[f"peer_{t.lower()}_score"] = fmt(sc)
    # And the key_deltas as JSON for the LLM. delta_*_pct is already a raw
    # percent (e.g. 38.0 for "38% above median"), NOT a decimal -- never *100.
    for kd in part_a["key_deltas"]:
        m = kd["metric"]
        d[f"delta_{m}_value"]      = fmt(kd["target_value"])
        d[f"delta_{m}_peer_median"] = fmt(kd["peer_median"])
        dp = kd["delta_pct"]
        d[f"delta_{m}_pct"]        = "n/a" if dp is None else f"{dp:+.1f}%"
        d[f"delta_{m}_rank"]       = kd["rank_label"]
    return d


def _peer_compare_table_for_prompt(target: str, part_a: dict) -> str:
    cols = ["pe", "ev_ebitda", "ps", "operating_margin", "roe", "roic",
            "revenue_growth", "eps_growth", "debt_equity", "fcf_margin"]
    lines = ["ticker | " + " | ".join(cols)]
    for t in part_a["ranked"]:
        vals = []
        for c in cols:
            v = part_a["by_ticker"][t].get(c)
            if v is None:
                vals.append("n/a")
            elif c in ("operating_margin", "roe", "roic", "revenue_growth",
                       "eps_growth", "fcf_margin") and isinstance(v, (int, float)):
                vals.append(f"{v*100:.1f}%")
            elif isinstance(v, float):
                vals.append(f"{v:.2f}")
            else:
                vals.append(str(v))
        marker = " *" if t == target else ""
        lines.append(f"{t}{marker} | " + " | ".join(vals))
    return "\n".join(lines)


def _generate_narrative(target: str, part_a: dict, part_b: dict, peer_descriptions: dict,
                         data_dict: dict) -> dict:
    """Returns dict with 7 sections (6 narrative + watchlist_peer_ticker)."""
    if not GROQ_API_KEY:
        return {}

    table = _peer_compare_table_for_prompt(target, part_a)
    deltas_lines = []
    for kd in part_a["key_deltas"]:
        deltas_lines.append(
            f"  {kd['metric']:<22} target={kd['target_value']:>8}  "
            f"peer_median={kd['peer_median']:>8}  delta={kd['delta_pct']}%  "
            f"({kd['rank_label']})"
        )
    industry_obs = (
        f"Cycle: {part_b['industry_cycle_position']}; "
        f"investability {part_b['investability_score']}/100; "
        f"sector ETF rel strength 120d {data_dict['industry_etf_relative_strength_120d']}; "
        f"median revenue growth {data_dict['industry_median_revenue_growth']}."
    )

    peer_descs = "\n".join(
        f"  - {t}: {(peer_descriptions.get(t) or '')[:200]}"
        for t in part_a["ranked"]
    )

    user_prompt = (
        f"Generate a peer comparison verdict for {target}.\n\n"
        f"PEER SET (target marked *):\n{peer_descs}\n\n"
        f"DATA (you may ONLY cite these keys via [DATA REF: key]):\n"
        + "\n".join(f"  {k}: {v}" for k, v in data_dict.items())
        + f"\n\nPEER COMPARISON TABLE:\n{table}\n\n"
        + f"KEY DELTAS:\n" + "\n".join(deltas_lines) + "\n\n"
        + f"WITHIN-INDUSTRY RANKING: {data_dict['within_industry_rank']}\n"
        + f"BEST-IN-INDUSTRY: {data_dict['best_in_industry_ticker']}\n"
        + f"TARGET'S COMPOSITE SCORE: {data_dict['overall_peer_score']}/100\n\n"
        + f"INDUSTRY CONTEXT:\n"
        + f"  industry_stance: {data_dict['industry_stance']}\n"
        + f"  cycle_position:  {data_dict['industry_cycle_position']}\n"
        + f"  investability_score: {data_dict['investability_score']}/100\n"
        + f"  observations: {industry_obs}\n\n"
        + (
            f"OUTPUT EXACTLY THESE SECTIONS as JSON with these keys:\n"
            f"  industry_stance_rationale  (1-2 sentences; cite specific industry data)\n"
            f"  peer_ranking_rationale     (1-2 sentences; cite 2-3 metric deltas)\n"
            f"  differentiation_thesis     (2 sentences; cite >= 2 metric advantages; "
            f"if target is NOT best-in-industry, explain why we still chose it over "
            f"{data_dict['best_in_industry_ticker']})\n"
            f"  relative_bull_case         (2 sentences; relative bull, cite advantages)\n"
            f"  relative_bear_case         (2 sentences; relative bear, cite disadvantages)\n"
            f"  watchlist_peer_ticker      (ONE ticker from the peer set)\n"
            f"  watchlist_peer_reasoning   (1-2 sentences; cite data)\n\n"
            f"EVERY SPECIFIC NUMBER MUST BE FOLLOWED BY [DATA REF: key_name] from the DATA section."
        )
    )

    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": _NARRATIVE_SYSTEM},
                      {"role": "user",   "content": user_prompt}],
            temperature=0.2, max_tokens=900,
            response_format={"type": "json_object"})
        content = (resp.choices[0].message.content or "").strip()
        return json.loads(content)
    except Exception as exc:
        log.warning("narrative generation failed for %s: %s", target, exc)
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# Persistence + watchlist auto-add
# ══════════════════════════════════════════════════════════════════════════════

def _persist(db, snapshot_date, run_type, target, peer_set_id, part_a, part_b,
             narrative, factcheck_result):
    payload = {
        "target_ticker":             target,
        "snapshot_date":             snapshot_date,
        "run_type":                  run_type,
        "peer_set_id":               peer_set_id,
        "peer_comparison":           {
            "by_ticker": {t: {k: v for k, v in m.items() if not k.startswith("_")}
                          for t, m in part_a["by_ticker"].items()},
            "percentile_ranks": part_a["ranks"],
            "overall_score":    part_a["overall"],
            "ranked":           part_a["ranked"],
        },
        "key_deltas":                part_a["key_deltas"],
        "overall_peer_score":        part_a["overall"][target],
        "within_industry_rank":      part_a["target_rank"],
        "peer_count":                len(part_a["ranked"]) - 1,
        "best_in_industry_ticker":   part_a["best"],
        "best_in_industry_is_target": part_a["best"] == target,
        "industry_metrics":          part_b["industry_metrics"],
        "industry_cycle_position":   part_b["industry_cycle_position"],
        "investability_score":       part_b["investability_score"],
        "industry_stance":           part_b["industry_stance"],
        "disruption_risk_score":     part_b["disruption_risk_score"],
        "industry_stance_rationale": narrative.get("industry_stance_rationale"),
        "peer_ranking_rationale":    narrative.get("peer_ranking_rationale"),
        "differentiation_thesis":    narrative.get("differentiation_thesis"),
        "relative_bull_case":        narrative.get("relative_bull_case"),
        "relative_bear_case":        narrative.get("relative_bear_case"),
        "watchlist_peer":            narrative.get("watchlist_peer_ticker"),
        "watchlist_peer_reasoning":  narrative.get("watchlist_peer_reasoning"),
        "verification_score":        factcheck_result.get("verification_score"),
        "quality_grade":             factcheck_result.get("quality_grade"),
    }
    try:
        db.table("peer_industry_analysis").upsert(
            [payload], on_conflict="target_ticker,snapshot_date,run_type"
        ).execute()
        return True
    except Exception as exc:
        log.warning("peer_industry_analysis persistence failed (%s) "
                    "-- apply migration 027", getattr(exc, "code", exc))
        return False


def _maybe_add_watchlist(db, snapshot_date, run_type, target, part_a):
    """If best_in_industry beats target by > WATCHLIST_DELTA_THRESHOLD, add
    best to auto_added_watchlist for the next pre-market run."""
    best = part_a["best"]
    if best == target:
        return False
    delta = part_a["overall"][best] - part_a["overall"][target]
    if delta < WATCHLIST_DELTA_THRESHOLD:
        return False
    try:
        db.table("auto_added_watchlist").upsert([{
            "ticker":          best,
            "source_ticker":   target,
            "source_run_date": snapshot_date,
            "source_run_type": run_type,
            "peer_score_delta": round(delta, 2),
            "reason": f"Peer score {round(part_a['overall'][best],1)} vs "
                      f"target {round(part_a['overall'][target],1)} "
                      f"(+{round(delta,1)}) -- promoted automatically.",
        }], on_conflict="ticker,source_ticker,source_run_date,source_run_type").execute()
        return True
    except Exception as exc:
        log.warning("auto_added_watchlist persistence failed (%s) "
                    "-- apply migration 028", getattr(exc, "code", exc))
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════════

def _print_metric_table(target: str, part_a: dict) -> None:
    cols = [("pe", "P/E"), ("ev_ebitda", "EV/EBITDA"), ("ps", "P/S"),
            ("operating_margin", "OpMgn%"), ("roe", "ROE%"),
            ("revenue_growth", "RevG%"), ("eps_growth", "EPSG%"),
            ("debt_equity", "D/E"), ("fcf_margin", "FCFM%")]
    header = f"  {'TICKER':<7}{'SCORE':>7}  " + " ".join(f"{c[1]:>9}" for c in cols)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for t in part_a["ranked"]:
        row = [f"{t:<7}", f"{part_a['overall'][t]:>6.1f} "]
        for k, _ in cols:
            v = part_a["by_ticker"][t].get(k)
            if v is None:
                row.append(f"{'n/a':>9}")
            elif k in ("operating_margin", "roe", "revenue_growth",
                       "eps_growth", "fcf_margin") and isinstance(v, (int, float)):
                row.append(f"{v*100:>8.1f}%")
            elif isinstance(v, float):
                row.append(f"{v:>9.2f}")
            else:
                row.append(f"{str(v):>9}")
        marker = " *" if t == target else "  "
        print(f"  {marker}" + "".join(row))


def _report(results: list[dict]) -> None:
    bar = "=" * 78
    print(f"\n{bar}\nPEER + INDUSTRY ANALYSIS  --  {len(results)} A-pick(s)\n{bar}")

    # Stance distribution.
    stance_dist = defaultdict(int)
    better_peer_count = 0
    for r in results:
        stance_dist[r["industry_stance"]] += 1
        if r["best_in_industry"] != r["target"]:
            better_peer_count += 1
    print("\n[ INDUSTRY STANCE DISTRIBUTION ]")
    for st in ("overweight", "market_weight", "underweight"):
        print(f"  {st:<14}{stance_dist[st]:>3}")
    print(f"\n[ PICKS WHERE BEST-IN-INDUSTRY != TARGET ]  {better_peer_count}/{len(results)}")
    for r in results:
        if r["best_in_industry"] != r["target"]:
            print(f"  {r['target']} -> best is {r['best_in_industry']} "
                  f"(score {r['best_score']:.1f} vs {r['target_score']:.1f})")

    for r in results:
        print(f"\n{bar}\n  TARGET: {r['target']}  --  industry: {r['industry']}  "
              f"--  stance: {r['industry_stance'].upper()}  "
              f"--  verify: {r['verification_score']} "
              f"({r['quality_grade']})\n{bar}")
        print(f"  Composite peer score: {r['target_score']:.1f}/100   "
              f"Industry rank: {r['target_rank']}/{r['n_universe']}   "
              f"Investability: {r['investability_score']}/100   "
              f"Cycle: {r['cycle_position']}")
        print(f"\n  Peer comparison table (target marked *):")
        _print_metric_table(r["target"], r["part_a"])
        print(f"\n  Industry stance rationale: "
              f"{strip_data_refs(r['narrative'].get('industry_stance_rationale') or '')}")
        print(f"\n  Differentiation thesis: "
              f"{strip_data_refs(r['narrative'].get('differentiation_thesis') or '')}")
        print(f"\n  Relative BULL: "
              f"{strip_data_refs(r['narrative'].get('relative_bull_case') or '')}")
        print(f"  Relative BEAR: "
              f"{strip_data_refs(r['narrative'].get('relative_bear_case') or '')}")
        print(f"  Watchlist peer: {r['narrative'].get('watchlist_peer_ticker')}  -- "
              f"{strip_data_refs(r['narrative'].get('watchlist_peer_reasoning') or '')}")

    # Quality gates summary
    print(f"\n{bar}\n[ QUALITY GATES ]")
    n = len(results)
    looser = n < 5
    gate_peers = sum(1 for r in results if 5 <= r["peer_count"] <= 8)
    print(f"  peer count in [5,8]:            {gate_peers}/{n} "
          f"({100*gate_peers/n if n else 0:.0f}%)")
    print(f"  verification >= 0.85:           "
          f"{sum(1 for r in results if (r['verification_score'] or 0) >= 0.85)}/{n}")
    stance_variety = len({r['industry_stance'] for r in results}) > 1
    print(f"  stance variety:                 "
          f"{'PASS' if stance_variety or looser else 'FAIL'}  "
          f"{'(loosened for N<5)' if looser and not stance_variety else ''}")
    better_peer = any(r['best_in_industry'] != r['target'] for r in results)
    print(f"  at least one better-peer found: "
          f"{'PASS' if better_peer or looser else 'FAIL'}  "
          f"{'(loosened for N<5)' if looser and not better_peer else ''}")
    print(bar + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday") -> None:
    if run_type not in ("premarket", "midday", "close"):
        run_type = "midday"
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Peer Industry Analysis -- run_type=%s", run_type)

    recent = (db.table("ranked_focus_list").select("run_date")
              .order("run_date", desc=True).limit(1).execute().data)
    if not recent:
        log.warning("ranked_focus_list empty.")
        return
    run_date = recent[0]["run_date"]

    apicks = (db.table("ranked_focus_list")
              .select("ticker,rank,conviction_grade")
              .eq("run_date", run_date).eq("run_type", run_type)
              .eq("conviction_grade", "A").order("rank").execute().data or [])
    if not apicks:
        log.warning("No A-grade picks today.")
        return

    info_cache = _InfoCache()
    results = []

    for p in apicks:
        target = p["ticker"]
        log.info("==== %s ====", target)
        peer_row = (db.table("peer_sets")
                    .select("id,peers,gics_industry,peer_count,peer_set_quality")
                    .eq("target_ticker", target).eq("snapshot_date", run_date)
                    .eq("run_type", run_type).limit(1).execute().data or [])
        if not peer_row:
            log.warning("no peer_set for %s -- skipping (run peer_definition first)",
                        target)
            continue
        peer_set_id = peer_row[0]["id"]
        peers = [p["ticker"] for p in (peer_row[0]["peers"] or [])]
        if not peers:
            log.warning("peer_set for %s is empty -- skipping", target)
            continue

        part_a = _compute_part_a(target, peers, info_cache)
        part_b = _compute_part_b(part_a["by_ticker"][target]["_sector"],
                                 part_a["by_ticker"][target]["_industry"],
                                 part_a["by_ticker"], peers)
        peer_descs = {t: part_a["by_ticker"][t].get("_long_summary")
                      for t in part_a["ranked"]}
        data_dict = _build_data_dict(target, part_a, part_b)
        narrative = _generate_narrative(target, part_a, part_b, peer_descs,
                                         data_dict)

        # Factcheck across all six narrative sections concatenated.
        joined = " ".join(str(narrative.get(k) or "") for k in (
            "industry_stance_rationale", "peer_ranking_rationale",
            "differentiation_thesis", "relative_bull_case",
            "relative_bear_case", "watchlist_peer_reasoning"))
        fc = factcheck(joined, data_dict)
        log.info("  factcheck: %d/%d claims verified  score=%s  grade=%s",
                 fc["verified_claims"], fc["total_claims"],
                 fc["verification_score"], fc["quality_grade"])

        _persist(db, run_date, run_type, target, peer_set_id, part_a, part_b,
                 narrative, fc)
        added = _maybe_add_watchlist(db, run_date, run_type, target, part_a)
        if added:
            log.info("  -> auto-added watchlist: %s (beats target by %.1f)",
                     part_a["best"],
                     part_a["overall"][part_a["best"]] - part_a["overall"][target])

        results.append({
            "target":            target,
            "industry":          part_a["by_ticker"][target]["_industry"],
            "target_rank":       part_a["target_rank"],
            "n_universe":        len(part_a["ranked"]),
            "best_in_industry":  part_a["best"],
            "target_score":      part_a["overall"][target],
            "best_score":        part_a["overall"][part_a["best"]],
            "industry_stance":   part_b["industry_stance"],
            "investability_score": part_b["investability_score"],
            "cycle_position":    part_b["industry_cycle_position"],
            "peer_count":        len(peers),
            "verification_score": fc["verification_score"],
            "quality_grade":     fc["quality_grade"],
            "narrative":         narrative,
            "part_a":            part_a,
            "part_b":            part_b,
        })

    _report(results)


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
