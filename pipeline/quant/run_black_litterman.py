"""
Quantitative Models Desk -- runner for Model 5 (Black-Litterman) plus the
Quant Models Verdict.

Sequence:
  1. run conviction_grader  -> A/B/C/D grades on ranked_focus_list
  2. Black-Litterman optimize the demo portfolio, views = A/B-grade picks
  3. build the Quant Models Verdict table for the top A-grade picks
  4. flag picks where the quant models DISAGREE with the bullish thesis

Usage:
    python pipeline/quant/run_black_litterman.py [premarket|midday|close]
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402
from agents import conviction_grader  # noqa: E402
from quant.black_litterman import BlackLittermanOptimizer, TXN_COST_BPS  # noqa: E402
from quant.utils import get_risk_free_rate  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

UNIVERSE_CAP  = 25
DEMO_NOTIONAL = 1_000_000.0
LIGHT = {"green": "GREEN", "amber": "AMBER", "red": "RED"}


def _fetch_all(query_fn, page=1000):
    rows, start = [], 0
    while True:
        chunk = query_fn().range(start, start + page - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        start += page


def run(run_type: str = "midday") -> None:
    if run_type not in ("premarket", "midday", "close"):
        run_type = "midday"

    # ── Step 1: conviction grades ─────────────────────────────────────────────
    log.info("=== Step 1: conviction_grader ===")
    try:
        conviction_grader.run(run_type)
    except Exception as exc:
        log.warning("conviction_grader failed: %s -- continuing", exc)

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    recent = (db.table("ranked_focus_list").select("run_date")
              .order("run_date", desc=True).limit(1).execute().data)
    if not recent:
        log.warning("ranked_focus_list empty."); return
    run_date = recent[0]["run_date"]
    picks = (db.table("ranked_focus_list").select(
             "ticker,rank,conviction_grade,conviction_score,quant_signal_summary,"
             "catalyst_description")
             .eq("run_date", run_date).eq("run_type", run_type)
             .order("rank").execute().data or [])
    by_ticker = {p["ticker"]: p for p in picks}
    top_tickers = [p["ticker"] for p in picks[:30]]

    # ── Build the BL universe: demo holdings + A/B-grade picks ────────────────
    # The demo must fit MAX_POSITION (5% cap) to start in-bounds -- a 10-name
    # 10%-each portfolio would violate the cap, making any rebalance within
    # the turnover budget mathematically infeasible.
    demo_holdings = {t: 0.05 for t in top_tickers[:20]}        # 20 x 5% = 100%
    ab = [p["ticker"] for p in picks
          if (p.get("conviction_grade") or "") in ("A", "B")]
    universe = list(dict.fromkeys(list(demo_holdings) + ab))[:UNIVERSE_CAP]
    log.info("BL universe: %d tickers (%d demo holdings, %d A/B picks)",
             len(universe), len(demo_holdings), len(ab))

    # ── Market data: prices, market caps, sectors ─────────────────────────────
    raw = yf.download(universe, period="3y", interval="1d", auto_adjust=True,
                      progress=False, group_by="ticker")
    multi = isinstance(raw.columns, pd.MultiIndex)
    prices, market_caps, sectors = {}, {}, {}
    for t in universe:
        try:
            prices[t] = (raw[t]["Close"] if multi else raw["Close"]).dropna()
        except (KeyError, TypeError):
            pass
        try:
            info = yf.Ticker(t).info or {}
            market_caps[t] = float(info.get("marketCap") or 0.0)
            sectors[t] = info.get("sector")
        except Exception:
            market_caps[t], sectors[t] = 0.0, None

    # ── DCF upside / pattern return for view magnitudes ───────────────────────
    dcf = {r["ticker"]: r for r in _fetch_all(lambda: db.table("dcf_valuations")
           .select("ticker,upside_to_dcf_pct,calculation_date")
           .order("calculation_date", desc=True))}
    pat = {r["ticker"]: r for r in _fetch_all(lambda: db.table("pattern_matches")
           .select("ticker,avg_5d_return,snapshot_date")
           .order("snapshot_date", desc=True))}

    # ── Black-Litterman ───────────────────────────────────────────────────────
    log.info("=== Step 2: Black-Litterman optimization ===")
    bl = BlackLittermanOptimizer()
    try:
        pi = bl.compute_market_equilibrium(universe, market_caps, prices)
    except Exception as exc:
        log.warning("equilibrium failed: %s", exc); return

    picks_with_grades = []
    for t in bl.tickers:
        p = by_ticker.get(t, {})
        picks_with_grades.append({
            "ticker": t,
            "conviction_grade": p.get("conviction_grade"),
            "dcf_upside": (dcf.get(t) or {}).get("upside_to_dcf_pct"),
            "pattern_5d_return": (pat.get(t) or {}).get("avg_5d_return"),
        })
    views = bl.specify_views(picks_with_grades)
    posterior = bl.bl_posterior(pi, bl.sigma, views)

    cur_w = np.array([demo_holdings.get(t, 0.0) for t in bl.tickers])
    cur_w = cur_w / cur_w.sum() if cur_w.sum() > 0 else cur_w
    opt = bl.optimize(posterior, bl.sigma, cur_w, sectors)

    quality_notes = []
    if opt["weights"] is None:
        log.warning("optimization infeasible: %s", opt.get("reason"))
        comparison, opt_w = None, None
        quality_notes.append(f"optimization infeasible -- {opt.get('reason')}")
    else:
        opt_w = opt["weights"]
        rf = get_risk_free_rate()
        comparison = bl.compare_portfolios(cur_w, opt_w, posterior, bl.sigma, rf)
        explanation = bl.explain_recommendations(comparison, views["applied"])
        if opt["quality"] == "feasible_but_constrained":
            quality_notes.append("position cap relaxed -- universe too small for 5%")
        if opt_w.max() > 0.20:
            quality_notes.append(f"extreme weight {opt_w.max()*100:.0f}% -- numerical concern")
        if not comparison["recommend_trade"]:
            quality_notes.append("net benefit negative after costs -- NO-TRADE recommended")
        # Tilt sanity: non-A names shouldn't deviate >5% from market weight --
        # but if the deviation is FORCED by the 5% position cap on a mega-cap
        # (market weight > cap), that's structural, not an active bet -- skip.
        max_w = opt.get("max_weight_used", 0.05)
        for i, t in enumerate(bl.tickers):
            grade = (by_ticker.get(t, {}).get("conviction_grade") or "")
            cap_forced = (abs(opt_w[i] - max_w) < 0.001
                          and bl.w_mkt[i] > max_w + 0.001)
            if grade != "A" and abs(opt_w[i] - bl.w_mkt[i]) > 0.05 and not cap_forced:
                quality_notes.append(f"{t}: non-A name tilted "
                                     f"{(opt_w[i]-bl.w_mkt[i])*100:+.0f}% vs market")

    # ── Persist ───────────────────────────────────────────────────────────────
    if comparison is not None:
        row = {
            "portfolio_id": None, "portfolio_label": "synthetic demo (top-10 EW)",
            "run_date": run_date, "run_type": run_type,
            "current_weights": {t: round(float(cur_w[i]), 4)
                                for i, t in enumerate(bl.tickers) if cur_w[i] > 0},
            "optimal_weights": {t: round(float(opt_w[i]), 4)
                                for i, t in enumerate(bl.tickers) if opt_w[i] > 0.005},
            "suggested_trades": comparison["trades"],
            "expected_return_current": comparison["expected_return_current"],
            "expected_return_optimal": comparison["expected_return_optimal"],
            "expected_volatility_current": comparison["expected_volatility_current"],
            "expected_volatility_optimal": comparison["expected_volatility_optimal"],
            "sharpe_current": comparison["sharpe_current"],
            "sharpe_optimal": comparison["sharpe_optimal"],
            "transaction_cost_estimate": comparison["transaction_cost_estimate"],
            "net_benefit": comparison["net_benefit"],
            "views_applied": views["applied"],
            "optimization_quality": opt["quality"],
            "explanation": explanation,
        }
        try:
            db.table("portfolio_optimization").insert(row).execute()
            log.info("portfolio_optimization: 1 row persisted")
        except Exception as exc:
            log.warning("portfolio_optimization not persisted (%s) -- apply migration 020",
                        getattr(exc, "code", exc))

    _report(run_date, by_ticker, bl, cur_w, opt_w, comparison, views,
            explanation if comparison is not None else None, quality_notes,
            dcf, pat)


# ══════════════════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════════════════

def _report(run_date, by_ticker, bl, cur_w, opt_w, comparison, views,
            explanation, quality_notes, dcf, pat) -> None:
    bar = "=" * 80
    print(f"\n{bar}\nBLACK-LITTERMAN  +  QUANT MODELS VERDICT  ({run_date})\n{bar}")

    # 1. BL optimization -----------------------------------------------------
    print("\n[ 1. BLACK-LITTERMAN OPTIMIZATION -- synthetic demo portfolio ]")
    if comparison is None:
        print("  optimization did not produce a solution -- see quality flags")
    else:
        c = comparison
        print(f"  Views applied: {len(views['applied'])} "
              f"(A/B-grade picks)  |  turnover {c['turnover']*100:.0f}%  |  "
              f"txn cost {c['transaction_cost_estimate']*100:.2f}%")
        print(f"  Expected return : {c['expected_return_current']*100:5.1f}%  ->  "
              f"{c['expected_return_optimal']*100:5.1f}%")
        print(f"  Volatility      : {c['expected_volatility_current']*100:5.1f}%  ->  "
              f"{c['expected_volatility_optimal']*100:5.1f}%")
        print(f"  Sharpe          : {c['sharpe_current']:5.2f}  ->  {c['sharpe_optimal']:5.2f}")
        print(f"  Net benefit (after cost): {c['net_benefit']*100:+.2f}%  -->  "
              f"{'TRADE' if c['recommend_trade'] else 'NO-TRADE'}")
        print("\n  Top suggested trades:")
        for tr in c["trades"][:8]:
            print(f"    {tr['action']:<5} {tr['ticker']:<7} "
                  f"{tr['current']*100:5.1f}% -> {tr['target']*100:5.1f}%  "
                  f"({tr['delta']*100:+.1f}%)")
        print(f"\n  {explanation}")

    # 2. Quant Models Verdict -- top 5 A-grade -------------------------------
    print(f"\n{bar}\n[ 2. QUANT MODELS VERDICT -- TOP 5 A-GRADE PICKS ]")
    a_picks = [t for t, p in sorted(by_ticker.items(),
               key=lambda kv: kv[1].get("rank") or 999)
               if (p.get("conviction_grade") or "") == "A"][:5]
    if not a_picks:
        print("  (no A-grade picks today)")
    for t in a_picks:
        summ = (by_ticker[t].get("quant_signal_summary") or {})
        lights = summ.get("model_lights", {})
        print(f"\n  {t}  (conviction A, score {summ.get('conviction_score')})")
        for model in ("monte_carlo", "var", "garch", "fama_french", "dcf"):
            lt = LIGHT.get(lights.get(model, "amber"), "AMBER")
            print(f"    {model:<14}{lt}")
        reds = sum(1 for v in lights.values() if v == "red")
        greens = sum(1 for v in lights.values() if v == "green")
        agree = ("STRONG agreement" if greens >= 3 and reds == 0
                 else "MIXED -- advisor review" if reds >= 2
                 else "moderate")
        print(f"    -> {greens} green / {reds} red  ::  {agree}")

    # 3. Quant-vs-thesis disagreements ---------------------------------------
    print(f"\n{bar}\n[ 3. QUANT MODELS DISAGREE WITH THE THESIS  (>=2 red lights) ]")
    disagree = []
    for t, p in by_ticker.items():
        lights = (p.get("quant_signal_summary") or {}).get("model_lights", {})
        reds = [m for m, v in lights.items() if v == "red"]
        if len(reds) >= 2:
            disagree.append((t, p.get("conviction_grade"), reds))
    if disagree:
        print("  These picks carry a bullish thesis but the quant models push back --")
        print("  the highest-value cases to review (alpha opportunity OR red flag):")
        for t, grade, reds in sorted(disagree, key=lambda x: -len(x[2])):
            print(f"    {t:<8} grade {grade or '?'}   red on: {', '.join(reds)}")
    else:
        print("  (none -- quant models broadly aligned with the focus list)")

    # 4. Quality-check failures ----------------------------------------------
    print(f"\n{bar}\n[ 4. QUALITY-CHECK FAILURES / FLAGS ]")
    if quality_notes:
        for n in quality_notes:
            print(f"  {n}")
    else:
        print("  (none -- optimization clean)")
    print(bar + "\n")


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
