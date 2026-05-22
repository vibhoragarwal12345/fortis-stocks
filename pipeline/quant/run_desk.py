"""
Quantitative Models Desk -- runner.
Runs Monte Carlo (Model 1) and VaR/CVaR (Model 2) for the top-ranked tickers,
then portfolio VaR + stress tests for the demo portfolio. Persists results to
monte_carlo_results / ticker_risk_metrics / portfolio_risk_metrics and prints
a report.

All numbers are deterministic Python; the LLM only writes Monte Carlo
explanations from already-computed values.

Usage:
    python pipeline/quant/run_desk.py [N]      # default N = 30 top tickers
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
from quant.models_config import (  # noqa: E402
    MC_HORIZONS, MIN_DATA_REQUIRED_DAYS, MIN_CALIBRATION_QUALITY,
    MONTE_CARLO_SIMS, COVERAGE_BAND, VAR_LOOKBACK_DAYS,
)
from quant.monte_carlo import MonteCarloSimulator  # noqa: E402
from quant.var_cvar import RiskModel  # noqa: E402
from quant.utils import get_risk_free_rate, safe_log_returns  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DEMO_NOTIONAL = 1_000_000.0     # synthetic demo-portfolio notional for $ VaR


# ══════════════════════════════════════════════════════════════════════════════
# Data
# ══════════════════════════════════════════════════════════════════════════════

def _load_top_tickers(db, n: int) -> tuple[str, list[str]]:
    recent = (db.table("ranked_focus_list").select("run_date")
              .order("run_date", desc=True).limit(1).execute().data)
    if not recent:
        return "", []
    run_date = recent[0]["run_date"]
    rows = (db.table("ranked_focus_list").select("ticker,rank")
            .eq("run_date", run_date).eq("run_type", "midday")
            .order("rank").limit(n).execute().data or [])
    return run_date, [r["ticker"] for r in rows]


def _download(tickers: list[str]) -> dict[str, pd.Series]:
    """3y of daily closes per ticker (+ SPY for beta)."""
    syms = list(dict.fromkeys(tickers + ["SPY"]))
    raw = yf.download(syms, period="3y", interval="1d", auto_adjust=True,
                      progress=False, group_by="ticker")
    out: dict[str, pd.Series] = {}
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in syms:
        try:
            close = (raw[t]["Close"] if multi else raw["Close"]).dropna()
            if len(close) > 0:
                out[t] = close
        except (KeyError, TypeError):
            pass
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Per-ticker model runs
# ══════════════════════════════════════════════════════════════════════════════

def _run_monte_carlo(ticker: str, prices: pd.Series) -> tuple[list[dict], dict]:
    """Returns (per-horizon result rows, quality dict)."""
    sim = MonteCarloSimulator()
    calib_prices = prices.tail(VAR_LOOKBACK_DAYS)
    cal = sim.calibrate(calib_prices)
    coverage = sim.backtest_coverage(prices, horizon=30)

    quality = {
        "calibration_quality": cal["calibration_quality"],
        "distribution_type":   cal["distribution_type"],
        "coverage":            coverage,
        "low_calibration":     cal["calibration_quality"] < MIN_CALIBRATION_QUALITY,
        "miscalibrated":       coverage is not None
                               and not (COVERAGE_BAND[0] <= coverage <= COVERAGE_BAND[1]),
    }

    start = float(prices.iloc[-1])
    rows = []
    for h in MC_HORIZONS:
        paths = sim.simulate(start, h, MONTE_CARLO_SIMS)
        summ = sim.summary(paths)
        explanation = sim.explain(summ, ticker, start) if h == 30 else None
        rows.append({
            "ticker": ticker, "horizon_days": h, "starting_price": round(start, 4),
            "distribution_type": cal["distribution_type"],
            "mean_price": summ["mean_price"], "median_price": summ["median_price"],
            "p5_price": summ["p5_price"], "p25_price": summ["p25_price"],
            "p75_price": summ["p75_price"], "p95_price": summ["p95_price"],
            "prob_up": summ["prob_up"], "prob_loss_10pct": summ["prob_loss_10pct"],
            "prob_gain_10pct": summ["prob_gain_10pct"],
            "expected_max_drawdown": summ["expected_max_drawdown"],
            "calibration_quality": cal["calibration_quality"],
            "explanation": explanation, "n_simulations": MONTE_CARLO_SIMS,
            "data_lookback_days": len(calib_prices),
        })
    return rows, quality


def _run_var(ticker: str, prices: pd.Series, market_returns: pd.Series,
             rm: RiskModel) -> tuple[dict, dict]:
    """Returns (ticker_risk_metrics row, quality dict)."""
    rets = safe_log_returns(prices.tail(VAR_LOOKBACK_DAYS))
    rets_1y = rets.tail(252)

    var95 = rm.historical_var(rets, 0.95, 1)
    var95, floored = rm.floored_var(var95)
    var99 = rm.historical_var(rets, 0.99, 1)
    var99 = rm.floored_var(var99)[0]
    cvar95 = rm.cvar(rets, 0.95)
    weekly = rm.historical_var(rets, 0.95, 5)
    exceed = rm.count_var_exceedances(rets_1y, var95)
    miscal = exceed < 2 or exceed > 25

    row = {
        "ticker": ticker,
        "daily_var_95":  -round(var95, 6),          # stored negative = loss
        "daily_var_99":  -round(var99, 6),
        "daily_cvar_95": -round(cvar95, 6),
        "weekly_var_95": -round(weekly, 6),
        "annual_volatility": round(rm.annual_volatility(rets), 6),
        "max_drawdown_1y":   round(rm.max_drawdown(prices.tail(252)), 6),
        "sharpe_ratio_1y":   round(rm.sharpe_ratio(rets_1y), 4),
        "sortino_ratio_1y":  round(rm.sortino_ratio(rets_1y), 4),
        "beta_vs_spy":       round(rm.beta(rets_1y, market_returns.tail(252)), 4),
        "var_floored":       floored,
        "var_exceedances_1y": exceed,
        "miscalibrated":     miscal,
        "data_quality_score": round(min(1.0, len(rets) / VAR_LOOKBACK_DAYS), 3),
    }
    return row, {"floored": floored, "miscalibrated": miscal, "exceedances": exceed}


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(n: int = 30) -> None:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    run_date, tickers = _load_top_tickers(db, n)
    if not tickers:
        log.warning("ranked_focus_list empty -- run ranking_engine first.")
        return
    log.info("Quant Desk -- %d tickers from %s", len(tickers), run_date)

    prices = _download(tickers)
    rf = get_risk_free_rate()
    log.info("Risk-free rate (3M T-bill): %.2f%%", rf * 100)
    rm = RiskModel(risk_free_rate=rf)
    market_returns = safe_log_returns(prices["SPY"]) if "SPY" in prices else pd.Series(dtype=float)

    mc_rows, risk_rows, mc_quality, risk_quality = [], [], {}, {}
    failures: list[tuple[str, str]] = []

    for t in tickers:
        p = prices.get(t)
        if p is None or len(p.dropna()) < MIN_DATA_REQUIRED_DAYS:
            n_days = 0 if p is None else len(p.dropna())
            failures.append((t, f"insufficient history ({n_days} < "
                                f"{MIN_DATA_REQUIRED_DAYS} days) -- models skipped"))
            log.warning("%s: SKIPPED -- only %d days of data", t, n_days)
            continue
        try:
            rows, q = _run_monte_carlo(t, p)
            mc_rows.extend(rows)
            mc_quality[t] = q
            vr, vq = _run_var(t, p, market_returns, rm)
            risk_rows.append(vr)
            risk_quality[t] = vq
        except Exception as exc:
            failures.append((t, f"model error: {exc}"))
            log.warning("%s: model error -- %s", t, exc)

    # ── Portfolio (demo) ──────────────────────────────────────────────────────
    portfolio = _resolve_portfolio(db, tickers)
    returns_map = {t: safe_log_returns(prices[t].tail(VAR_LOOKBACK_DAYS))
                   for t in portfolio["holdings"] if t in prices}
    pvar = rm.portfolio_var(portfolio["holdings"], VAR_LOOKBACK_DAYS, returns_map)
    stress = rm.stress_test(portfolio["holdings"])
    portfolio_row = _portfolio_row(portfolio, pvar, stress)

    # ── Persist ───────────────────────────────────────────────────────────────
    _persist(db, run_date, mc_rows, risk_rows, portfolio_row)

    # ── Report ────────────────────────────────────────────────────────────────
    _report(tickers, mc_rows, risk_rows, mc_quality, risk_quality,
            portfolio, pvar, stress, failures)


def _resolve_portfolio(db, top_tickers: list[str]) -> dict:
    """Use a real user portfolio if one exists, else a synthetic equal-weight
    demo of the top 10 ranked tickers."""
    try:
        pf = db.table("portfolios").select("id,name").limit(1).execute().data
        if pf:
            pid = pf[0]["id"]
            hold = (db.table("portfolio_holdings")
                    .select("ticker,shares,cost_basis").eq("portfolio_id", pid)
                    .execute().data or [])
            if hold:
                total = sum(float(h.get("shares") or 0) for h in hold) or 1.0
                weights = {h["ticker"]: float(h.get("shares") or 0) / total
                           for h in hold}
                return {"holdings": weights, "label": pf[0].get("name") or "user portfolio",
                        "portfolio_id": pid, "notional": None}
    except Exception as exc:
        log.warning("portfolio lookup failed: %s", exc)

    top10 = top_tickers[:10]
    weights = {t: 1.0 / len(top10) for t in top10}
    log.info("No user portfolio found -- using synthetic equal-weight demo "
             "(top %d, $%.0fM notional)", len(top10), DEMO_NOTIONAL / 1e6)
    return {"holdings": weights, "label": "synthetic demo (top-10 equal weight)",
            "portfolio_id": None, "notional": DEMO_NOTIONAL}


def _portfolio_row(portfolio: dict, pvar: dict, stress: dict) -> dict:
    notional = portfolio.get("notional")
    contrib = sorted(pvar.get("per_position_marginal_var", {}).items(),
                     key=lambda kv: kv[1], reverse=True)[:3]

    def _usd(pct):
        return round(pct * notional, 2) if (notional and pct is not None) else None

    return {
        "portfolio_id":     portfolio.get("portfolio_id"),
        "portfolio_label":  portfolio["label"],
        "portfolio_notional_usd": notional,
        "portfolio_var_95_1d_pct": pvar.get("portfolio_var_95"),
        "portfolio_var_95_1d_usd": _usd(pvar.get("portfolio_var_95")),
        "portfolio_var_99_1d_pct": pvar.get("portfolio_var_99"),
        "portfolio_var_99_1d_usd": _usd(pvar.get("portfolio_var_99")),
        "portfolio_cvar_95_pct":   pvar.get("portfolio_cvar_95"),
        "portfolio_cvar_95_usd":   _usd(pvar.get("portfolio_cvar_95")),
        "annual_volatility":       pvar.get("annual_volatility"),
        "diversification_ratio":   pvar.get("diversification_ratio"),
        "stress_test_gfc_2008":    (stress.get("gfc_2008") or {}).get("estimated_drawdown_pct"),
        "stress_test_covid_2020":  (stress.get("covid_2020") or {}).get("estimated_drawdown_pct"),
        "stress_test_rate_shock_2022": (stress.get("rate_shock_2022") or {}).get("estimated_drawdown_pct"),
        "top_3_risk_contributors": [{"ticker": t, "component_var": v} for t, v in contrib],
    }


def _persist(db, run_date, mc_rows, risk_rows, portfolio_row) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        for r in mc_rows:
            r["run_date"] = run_date
            r["run_type"] = "midday"
        if mc_rows:
            db.table("monte_carlo_results").upsert(
                mc_rows, on_conflict="ticker,run_date,run_type,horizon_days").execute()
        log.info("monte_carlo_results: %d rows persisted", len(mc_rows))
    except Exception as exc:
        log.warning("monte_carlo_results not persisted (%s) -- apply migration 013",
                    getattr(exc, "code", exc))
    try:
        for r in risk_rows:
            r["calculation_date"] = today
        if risk_rows:
            db.table("ticker_risk_metrics").upsert(
                risk_rows, on_conflict="ticker,calculation_date").execute()
        log.info("ticker_risk_metrics: %d rows persisted", len(risk_rows))
    except Exception as exc:
        log.warning("ticker_risk_metrics not persisted (%s) -- apply migration 014",
                    getattr(exc, "code", exc))
    try:
        portfolio_row["calculation_date"] = today
        db.table("portfolio_risk_metrics").insert(portfolio_row).execute()
        log.info("portfolio_risk_metrics: 1 row persisted")
    except Exception as exc:
        log.warning("portfolio_risk_metrics not persisted (%s) -- apply migration 014",
                    getattr(exc, "code", exc))


# ══════════════════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════════════════

def _report(tickers, mc_rows, risk_rows, mc_quality, risk_quality,
            portfolio, pvar, stress, failures) -> None:
    bar = "=" * 80
    mc30 = {r["ticker"]: r for r in mc_rows if r["horizon_days"] == 30}
    risk = {r["ticker"]: r for r in risk_rows}
    top5 = [t for t in tickers if t in mc30][:5]

    print(f"\n{bar}\nQUANTITATIVE MODELS DESK\n{bar}")

    print("\n[ 1. MONTE CARLO -- TOP 5 PICKS (30-day horizon) ]")
    for t in top5:
        r = mc30[t]
        print(f"\n  {t}  start ${r['starting_price']:.2f}  ({r['distribution_type']}, "
              f"calibration {r['calibration_quality']:.2f})")
        print(f"    median ${r['median_price']:.2f}  |  5th-95th "
              f"${r['p5_price']:.2f}-${r['p95_price']:.2f}  |  prob_up "
              f"{r['prob_up']:.0%}  |  P(>10% drop) {r['prob_loss_10pct']:.0%}")
        print(f"    {r['explanation']}")

    print(f"\n{bar}\n[ 2. VaR / CVaR -- TOP 5 PICKS ]")
    print(f"  {'TICKER':<8}{'VaR95 1d':>11}{'VaR99 1d':>11}{'CVaR95':>10}"
          f"{'ANN VOL':>10}{'BETA':>8}{'SHARPE':>9}")
    for t in top5:
        r = risk.get(t)
        if not r:
            continue
        print(f"  {t:<8}{r['daily_var_95']*100:>10.2f}%{r['daily_var_99']*100:>10.2f}%"
              f"{r['daily_cvar_95']*100:>9.2f}%{r['annual_volatility']*100:>9.1f}%"
              f"{r['beta_vs_spy']:>8.2f}{r['sharpe_ratio_1y']:>9.2f}")

    print(f"\n{bar}\n[ 3. PORTFOLIO RISK -- {portfolio['label']} ]")
    notional = portfolio.get("notional")
    if pvar:
        print(f"  Holdings: {', '.join(pvar.get('tickers_used', []))}")
        v95, v99 = pvar.get("portfolio_var_95"), pvar.get("portfolio_var_99")
        cv = pvar.get("portfolio_cvar_95")
        print(f"  1-day VaR 95%: {v95*100:.2f}%"
              + (f"  (${v95*notional:,.0f})" if notional else ""))
        print(f"  1-day VaR 99%: {v99*100:.2f}%"
              + (f"  (${v99*notional:,.0f})" if notional else ""))
        print(f"  1-day CVaR 95%: {cv*100:.2f}%"
              + (f"  (${cv*notional:,.0f})" if notional else ""))
        print(f"  Annualized vol: {pvar.get('annual_volatility', 0)*100:.1f}%  |  "
              f"Diversification ratio: {pvar.get('diversification_ratio')}")
        contrib = sorted(pvar.get("per_position_marginal_var", {}).items(),
                         key=lambda kv: kv[1], reverse=True)[:3]
        print("  Top-3 risk contributors (component VaR): "
              + ", ".join(f"{t} {v*100:.2f}%" for t, v in contrib))
    print("\n  Stress tests (estimated portfolio drawdown):")
    for name, res in stress.items():
        dd = res.get("estimated_drawdown_pct")
        cov = f"{res.get('tickers_covered')}/{res.get('tickers_total')} holdings"
        print(f"    {name:<18}{(f'{dd:+.1f}%' if dd is not None else 'n/a'):>10}"
              f"   ({cov} had data)")

    print(f"\n{bar}\n[ 4. QUALITY-CHECK FAILURES / FLAGS ]")
    if failures:
        for t, reason in failures:
            print(f"  SKIPPED  {t:<8} {reason}")
    flagged = False
    for t in tickers:
        notes = []
        q = mc_quality.get(t)
        if q and q["low_calibration"]:
            notes.append(f"low MC calibration {q['calibration_quality']:.2f} "
                         f"(<{MIN_CALIBRATION_QUALITY}) -> excluded from A-grade")
        if q and q["miscalibrated"]:
            notes.append(f"MC 90% CI coverage {q['coverage']} outside "
                         f"{COVERAGE_BAND} -> miscalibrated")
        rq = risk_quality.get(t)
        if rq and rq["floored"]:
            notes.append("VaR floored at 50bps (sub-floor value = data artifact)")
        if rq and rq["miscalibrated"]:
            notes.append(f"VaR exceedances {rq['exceedances']}/yr outside 2-25 "
                         f"-> miscalibrated")
        if notes:
            flagged = True
            print(f"  FLAG     {t:<8} {'; '.join(notes)}")
    if not failures and not flagged:
        print("  (none -- all tickers passed)")
    print(bar + "\n")


if __name__ == "__main__":
    arg = 30
    if len(sys.argv) > 1:
        try:
            arg = int(sys.argv[1])
        except ValueError:
            pass
    run(arg)
