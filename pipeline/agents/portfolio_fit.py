"""
Portfolio Fit
For every user portfolio in our system, scores each top-30 ranked candidate
against the portfolio's actual holdings. The output isn't "is this stock
good?" -- the ranking_engine answers that. It's "is this stock a good fit
for THIS portfolio, given what's already in it?".

Per (portfolio, candidate) computation:

  already_held         is the candidate in current holdings?
  current_weight       fraction of portfolio value, if held
  unrealized_pnl       (current_price - cost_basis) / cost_basis
  sector_fit_score     0-100: would adding push any sector over 35%?
  correlation_warnings [{holding, corr}] for any holding with 90-day rho > 0.85
  style_alignment      aligned / mild_drift / significant_drift (vs portfolio
                       style centroid from factor_exposure)
  risk_contribution    portfolio vol delta if added at 2% (annualized %)
  recommended_size     position-size suggestion (0..4%) based on conviction
                       grade and correlation overlap
  recommendation       high_priority | consider | pass | already_holding

Usage:
    python pipeline/agents/portfolio_fit.py [run_type]      # default midday
"""

import logging
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

TOP_N           = 30
RETURN_LOOKBACK = 90       # daily returns for correlation + portfolio vol
SECTOR_CAP      = 0.35     # max sector concentration we tolerate
CORR_WARN       = 0.85     # corr above this is a diversification red flag
PROBE_WEIGHT    = 0.02     # candidate weight used in risk-contribution probe

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Loaders
# ══════════════════════════════════════════════════════════════════════════════

def _load_top_picks(db, run_type: str):
    recent = (db.table("ranked_focus_list").select("run_date")
              .order("run_date", desc=True).limit(1).execute().data)
    if not recent:
        return None, []
    rd = recent[0]["run_date"]
    rows = (db.table("ranked_focus_list")
            .select("ticker,rank,conviction_grade,composite_score")
            .eq("run_date", rd).eq("run_type", run_type)
            .order("rank").limit(TOP_N).execute().data or [])
    return rd, rows


def _load_portfolios(db):
    return db.table("portfolios").select("id,name,user_id").execute().data or []


def _load_holdings(db, portfolio_id):
    return (db.table("portfolio_holdings")
            .select("ticker,shares,cost_basis")
            .eq("portfolio_id", portfolio_id).execute().data or [])


def _fetch_sector(ticker: str, cache: dict) -> str | None:
    if ticker in cache:
        return cache[ticker]
    try:
        sector = (yf.Ticker(ticker).info or {}).get("sector")
    except Exception:
        sector = None
    cache[ticker] = sector
    time.sleep(0.1)
    return sector


# ══════════════════════════════════════════════════════════════════════════════
# Per-portfolio analysis
# ══════════════════════════════════════════════════════════════════════════════

def _analyze_portfolio(db, portfolio: dict, picks: list[dict],
                      sector_cache: dict) -> list[dict]:
    holdings = _load_holdings(db, portfolio["id"])
    if not holdings:
        log.warning("portfolio %s has no holdings -- skipping", portfolio["id"])
        return []
    h_tickers = [h["ticker"] for h in holdings]
    p_tickers = [p["ticker"] for p in picks]
    all_tickers = list(dict.fromkeys(h_tickers + p_tickers + ["SPY"]))
    log.info("portfolio %s -- %d holdings, %d candidates",
             portfolio["name"], len(holdings), len(picks))

    # ── Price & return data ──────────────────────────────────────────────────
    raw = yf.download(all_tickers, period="1y", interval="1d",
                      auto_adjust=True, progress=False, group_by="ticker")
    multi = isinstance(raw.columns, pd.MultiIndex)
    closes = {}
    for t in all_tickers:
        try:
            s = (raw[t]["Close"] if multi else raw["Close"]).dropna()
            if len(s) > 0:
                closes[t] = s
        except (KeyError, TypeError):
            pass
    rets_all = (pd.DataFrame({t: closes[t].pct_change().dropna()
                              for t in closes if len(closes[t]) > 1})
                .tail(RETURN_LOOKBACK).dropna(how="all"))

    # ── Current portfolio state ──────────────────────────────────────────────
    cur_prices = {t: float(closes[t].iloc[-1]) for t in h_tickers if t in closes}
    h_values = {h["ticker"]: cur_prices.get(h["ticker"], 0.0) * float(h["shares"])
                for h in holdings}
    total_value = sum(h_values.values()) or 1.0
    weights = {t: v / total_value for t, v in h_values.items()}
    cost_basis = {h["ticker"]: float(h["cost_basis"]) for h in holdings}

    # Sector weights of the current portfolio.
    for t in h_tickers:
        _fetch_sector(t, sector_cache)
    sector_weights = {}
    for t, w in weights.items():
        s = sector_cache.get(t) or "Unknown"
        sector_weights[s] = sector_weights.get(s, 0.0) + w

    # Portfolio return series + current vol.
    w_vec = np.array([weights.get(t, 0.0) for t in rets_all.columns])
    port_ret = rets_all.to_numpy() @ w_vec
    port_vol_current = float(np.std(port_ret, ddof=1) * math.sqrt(252))

    # ── Style centroid from factor_exposure ─────────────────────────────────
    factor_rows = (db.table("factor_exposure")
                   .select("ticker,value_exposure,growth_exposure,quality_exposure,"
                           "momentum_exposure,low_vol_exposure")
                   .in_("ticker", h_tickers + p_tickers).execute().data or [])
    style_by = {r["ticker"]: r for r in factor_rows}

    def _style_vec(ticker):
        r = style_by.get(ticker)
        if not r:
            return None
        v = [r.get(k) for k in ("value_exposure", "growth_exposure",
                                "quality_exposure", "momentum_exposure",
                                "low_vol_exposure")]
        return np.array([float(x) if x is not None else 0.0 for x in v])

    style_vecs = [_style_vec(t) for t in h_tickers]
    style_vecs = [(t, v, weights.get(t, 0.0)) for t, v in zip(h_tickers, style_vecs) if v is not None]
    if style_vecs:
        ws = np.array([w for _, _, w in style_vecs])
        ws = ws / ws.sum() if ws.sum() > 0 else ws
        portfolio_centroid = sum(w * v for (_, v, _), w in zip(style_vecs, ws))
    else:
        portfolio_centroid = None

    # ── Per-candidate scoring ────────────────────────────────────────────────
    today = datetime.now(timezone.utc).date().isoformat()
    results = []
    for p in picks:
        t = p["ticker"]
        held = t in h_values
        cur_w = weights.get(t, 0.0)
        unreal = None
        if held and cost_basis.get(t):
            cur = cur_prices.get(t, 0.0)
            unreal = (cur - cost_basis[t]) / cost_basis[t] if cost_basis[t] else None

        # Sector fit: add 2% candidate, scale holdings to 98%.
        cand_sector = _fetch_sector(t, sector_cache) or "Unknown"
        scaled_sectors = {s: w * (1 - PROBE_WEIGHT) for s, w in sector_weights.items()}
        scaled_sectors[cand_sector] = scaled_sectors.get(cand_sector, 0.0) + PROBE_WEIGHT
        max_sector_w = max(scaled_sectors.values()) if scaled_sectors else 0.0
        if max_sector_w <= SECTOR_CAP:
            sector_fit = 100.0
        else:
            sector_fit = max(0.0, 100.0 - (max_sector_w - SECTOR_CAP) * 400.0)

        # Correlation warnings: rho > CORR_WARN with any holding.
        warnings = []
        if t in rets_all.columns:
            for h in h_tickers:
                if h == t or h not in rets_all.columns:
                    continue
                rho = float(rets_all[t].corr(rets_all[h]))
                if rho == rho and rho > CORR_WARN:
                    warnings.append({"holding": h, "corr": round(rho, 3)})

        # Risk contribution: vol(0.98 x holdings + 0.02 x candidate) - current.
        if t in rets_all.columns:
            probe_w = np.array([weights.get(c, 0.0) * (1 - PROBE_WEIGHT)
                                + (PROBE_WEIGHT if c == t else 0.0)
                                for c in rets_all.columns])
            probe_ret = rets_all.to_numpy() @ probe_w
            probe_vol = float(np.std(probe_ret, ddof=1) * math.sqrt(252))
            risk_contrib = probe_vol - port_vol_current
        else:
            risk_contrib = None

        # Style alignment: Euclidean distance from portfolio centroid.
        cand_vec = _style_vec(t)
        if cand_vec is not None and portfolio_centroid is not None:
            dist = float(np.linalg.norm(cand_vec - portfolio_centroid))
            style = ("aligned" if dist < 0.40 else
                     "mild_drift" if dist < 0.80 else "significant_drift")
        else:
            style = "unknown"

        # Position-size & recommendation logic.
        grade = (p.get("conviction_grade") or "C").upper()
        max_corr = max((w["corr"] for w in warnings), default=0.0)
        if grade == "A":
            base_size = 0.04 if max_corr < 0.70 else (0.02 if max_corr < 0.85 else 0.015)
        elif grade == "B":
            base_size = 0.025 if max_corr < 0.70 else 0.015
        else:
            base_size = 0.015 if max_corr < 0.70 else 0.010

        # Recommendation.
        if held:
            rec = "already_holding"
        elif grade == "A" and not warnings and sector_fit >= 90:
            rec = "high_priority"
        elif grade in ("A", "B") and sector_fit >= 70:
            rec = "consider"
        else:
            rec = "pass"

        results.append({
            "portfolio_id":         portfolio["id"],
            "ticker":               t,
            "snapshot_date":        today,
            "already_held":         held,
            "current_weight":       round(cur_w, 4) if held else 0.0,
            "unrealized_pnl":       round(unreal, 4) if unreal is not None else None,
            "sector_fit_score":     round(sector_fit, 1),
            "correlation_warnings": warnings,
            "style_alignment":      style,
            "risk_contribution":    round(risk_contrib, 5) if risk_contrib is not None else None,
            "recommended_size":     round(base_size, 4),
            "recommendation":       rec,
            "_grade":               grade,
            "_max_corr":            round(max_corr, 3) if max_corr else 0.0,
            "_max_sector":          round(max_sector_w, 3),
            "_cand_sector":         cand_sector,
        })
    return results


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday") -> None:
    if run_type not in ("premarket", "midday", "close"):
        run_type = "midday"
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    run_date, picks = _load_top_picks(db, run_type)
    if not picks:
        log.warning("ranked_focus_list empty -- run ranking_engine first.")
        return
    log.info("Loaded %d candidates from %s/%s", len(picks), run_date, run_type)

    portfolios = _load_portfolios(db)
    if not portfolios:
        log.warning("No portfolios in DB -- run pipeline/data/load_sample_portfolio.py")
        return
    log.info("Found %d portfolio(s)", len(portfolios))

    sector_cache: dict = {}
    for portfolio in portfolios:
        results = _analyze_portfolio(db, portfolio, picks, sector_cache)
        if not results:
            continue
        # Persist (strip the underscore-prefixed report-only keys).
        payload = [{k: v for k, v in r.items() if not k.startswith("_")}
                   for r in results]
        try:
            db.table("portfolio_fit").upsert(
                payload, on_conflict="portfolio_id,ticker,snapshot_date").execute()
            log.info("portfolio_fit: %d rows persisted for %s",
                     len(payload), portfolio["name"])
        except Exception as exc:
            log.warning("portfolio_fit not persisted (%s) -- apply migration 023",
                        getattr(exc, "code", exc))

        _report(portfolio, results)


def _report(portfolio: dict, results: list[dict]) -> None:
    bar = "=" * 78
    print(f"\n{bar}\nPORTFOLIO FIT  --  {portfolio['name']}\n{bar}")

    top5 = results[:5]
    print(f"\n[ TOP 5 RANKED CANDIDATES vs PORTFOLIO ]")
    for r in top5:
        held = "  [held]" if r["already_held"] else ""
        print(f"\n  {r['ticker']:<8} grade {r['_grade']}{held}")
        print(f"    recommendation     : {r['recommendation'].upper()}")
        print(f"    recommended_size   : {r['recommended_size']*100:.1f}%")
        print(f"    sector             : {r['_cand_sector']:<26} "
              f"sector_fit={r['sector_fit_score']:.0f}/100  "
              f"(max sector after add: {r['_max_sector']*100:.0f}%)")
        print(f"    style_alignment    : {r['style_alignment']}")
        if r['risk_contribution'] is not None:
            arrow = "UP" if r['risk_contribution'] > 0 else "DOWN"
            print(f"    risk_contribution  : {arrow} "
                  f"{r['risk_contribution']*100:+.2f}% vol")
        if r['correlation_warnings']:
            top_corr = sorted(r['correlation_warnings'],
                              key=lambda x: -x['corr'])[:3]
            cs = ", ".join(f"{c['holding']} ({c['corr']:.2f})" for c in top_corr)
            print(f"    correlation WARN   : {cs}")
        if r['already_held']:
            print(f"    current_weight     : {r['current_weight']*100:.1f}%")
            if r['unrealized_pnl'] is not None:
                print(f"    unrealized_pnl     : {r['unrealized_pnl']*100:+.1f}%")

    print(f"\n[ RECOMMENDATION DISTRIBUTION across {len(results)} candidates ]")
    from collections import Counter
    rec_dist = Counter(r["recommendation"] for r in results)
    for rec in ("high_priority", "consider", "pass", "already_holding"):
        if rec in rec_dist:
            print(f"  {rec:<20}{rec_dist[rec]:>4}")
    print(bar + "\n")


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
