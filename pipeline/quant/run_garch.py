"""
Quantitative Models Desk -- GARCH runner.
Fits GARCH(1,1) volatility forecasts for the top-ranked tickers, compares the
forecast to options-implied volatility, classifies the regime, runs the
quality checks, persists to garch_forecasts and prints a report.

Usage:
    python pipeline/quant/run_garch.py [N]      # default N = 30
"""

import logging
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
from quant.models_config import MIN_DATA_REQUIRED_DAYS, TRADING_DAYS_PER_YEAR  # noqa: E402
from quant.garch import GARCHModel  # noqa: E402
from quant.utils import safe_log_returns  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


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
    # 5 years: a longer window stabilizes GARCH's boundary (near-unit-root) fits.
    raw = yf.download(tickers, period="5y", interval="1d", auto_adjust=True,
                      progress=False, group_by="ticker")
    out: dict[str, pd.Series] = {}
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in tickers:
        try:
            close = (raw[t]["Close"] if multi else raw["Close"]).dropna()
            if len(close) > 0:
                out[t] = close
        except (KeyError, TypeError):
            pass
    return out


def run(n: int = 30) -> None:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    run_date, tickers = _load_top_tickers(db, n)
    if not tickers:
        log.warning("ranked_focus_list empty -- run ranking_engine first.")
        return
    log.info("GARCH desk -- %d tickers from %s", len(tickers), run_date)

    prices = _download(tickers)
    results: list[dict] = []
    failures: list[tuple[str, str]] = []

    for t in tickers:
        p = prices.get(t)
        if p is None or len(p.dropna()) < MIN_DATA_REQUIRED_DAYS:
            n_days = 0 if p is None else len(p.dropna())
            failures.append((t, f"insufficient history ({n_days} < "
                                f"{MIN_DATA_REQUIRED_DAYS}) -- GARCH skipped"))
            continue
        try:
            rets = safe_log_returns(p)
            g = GARCHModel()
            fit_info = g.fit(rets)
            fc = g.forecast(22)
            regime = g.current_regime()
            cmp = g.compare_to_iv(t)               # ATM IV via Black-Scholes inversion
            time.sleep(0.2)                        # polite to Yahoo's option API
            bt = g.backtest(rets)
            quality = g.assess_quality(len(rets), bt["ok"],
                                       fc.get("forecast_clamped", False))
            realized_30d = float(rets.tail(30).std(ddof=1)
                                 * np.sqrt(TRADING_DAYS_PER_YEAR))

            iv_meta = cmp["iv_meta"]
            iv_quality = iv_meta.get("quality")
            # A low-quality IV must not leave the model graded 'reliable'.
            if iv_quality == "low" and quality == "reliable":
                quality = "use_with_caution"
            iv_source = ("low_quality" if iv_quality == "low"
                         else "computed_from_bid_ask" if cmp["iv"] is not None
                         else "unavailable")

            explain_input = {**fc, "regime": regime["regime"],
                             "iv": cmp["iv"], "spread": cmp["spread"],
                             "verdict": cmp["verdict"]}
            explanation = g.explain(explain_input, t)

            results.append({
                "ticker": t,
                "model_alpha": fit_info["alpha"], "model_beta": fit_info["beta"],
                "model_persistence": fit_info["persistence"],
                "realized_vol_30d": round(realized_30d, 6),
                "forecast_vol_1d": fc["forecast_vol_1d"],
                "forecast_vol_5d": fc["forecast_vol_5d"],
                "forecast_vol_22d": fc["forecast_vol_22d"],
                "unconditional_vol": fit_info["unconditional_vol_annual"],
                "regime": regime["regime"],
                "iv_vs_garch_spread": cmp["spread"],
                "iv_source": iv_source,
                "iv_strike_used": iv_meta.get("strike"),
                "iv_days_to_expiry": iv_meta.get("days_to_expiry"),
                "iv_parity_spread": iv_meta.get("parity_spread"),
                "iv_quality": iv_quality,
                "model_quality": quality,
                "explanation": explanation,
                "_arch_lm_p": fit_info["arch_lm_p"],
                "_backtest": bt, "_n": len(rets),
                "_forecast_clamped": fc.get("forecast_clamped", False),
                "_iv": cmp["iv"],
            })
            log.info("%s: 30d vol %.1f%% | regime %s | quality %s", t,
                     fc["forecast_vol_22d"] * 100, regime["regime"], quality)
        except Exception as exc:
            failures.append((t, f"GARCH error: {exc}"))
            log.warning("%s: GARCH error -- %s", t, exc)

    _persist(db, run_date, results)
    _report(tickers, results, failures)


def _persist(db, run_date: str, results: list[dict]) -> None:
    if not results:
        return
    rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
    for r in rows:
        r["forecast_date"] = run_date
    try:
        db.table("garch_forecasts").upsert(
            rows, on_conflict="ticker,forecast_date").execute()
        log.info("garch_forecasts: %d rows persisted", len(rows))
    except Exception as exc:
        log.warning("garch_forecasts not persisted (%s) -- apply migration 015",
                    getattr(exc, "code", exc))


def _report(tickers, results, failures) -> None:
    bar = "=" * 78
    by_ticker = {r["ticker"]: r for r in results}
    top5 = [t for t in tickers if t in by_ticker][:5]
    print(f"\n{bar}\nGARCH VOLATILITY FORECASTS -- {len(results)} tickers\n{bar}")

    print("\n[ 1. 30-DAY FORECAST VOLATILITY -- TOP 5 ]")
    for t in top5:
        r = by_ticker[t]
        print(f"\n  {t}  forecast 30d vol {r['forecast_vol_22d']*100:.1f}%  "
              f"(realized 30d {r['realized_vol_30d']*100:.1f}%, "
              f"unconditional {r['unconditional_vol']*100:.1f}%)")
        print(f"    persistence {r['model_persistence']:.3f} | regime "
              f"{r['regime']} | quality {r['model_quality']}")
        print(f"    {r['explanation']}")

    print(f"\n{bar}\n[ 2. REGIME DISTRIBUTION ]")
    dist = Counter(r["regime"] for r in results)
    for regime in ("low", "normal", "elevated", "crisis"):
        print(f"  {regime:<10}{dist.get(regime, 0):>4}")

    n_iv = sum(1 for r in results if r.get("_iv") is not None)
    print(f"\n{bar}\n[ 3. LARGEST IV-vs-GARCH SPREADS (>3%) -- "
          f"real ATM IV available for {n_iv}/{len(results)} ]")
    spreads = [r for r in results if r["iv_vs_garch_spread"] is not None
               and abs(r["iv_vs_garch_spread"]) > 0.03]
    spreads.sort(key=lambda r: abs(r["iv_vs_garch_spread"]), reverse=True)
    if spreads:
        print(f"  {'TICKER':<8}{'IV-GARCH':>11}  SIGNAL")
        for r in spreads:
            sp = r["iv_vs_garch_spread"]
            signal = ("options expensive -- premium-selling edge" if sp > 0
                      else "options cheap -- long-options edge")
            print(f"  {r['ticker']:<8}{sp*100:>+10.1f}%  {signal}")
    else:
        print("  (none cleared the 3% threshold)")

    print(f"\n{bar}\n[ 4. QUALITY-CHECK FAILURES / FLAGS ]")
    if failures:
        for t, reason in failures:
            print(f"  SKIPPED  {t:<8} {reason}")
    flagged = False
    for r in results:
        if r["model_quality"] != "reliable":
            flagged = True
            reasons = []
            if r["model_persistence"] > 0.99:
                reasons.append(f"persistence {r['model_persistence']:.3f} >0.99 (unstable)")
            if r["_arch_lm_p"] is not None and r["_arch_lm_p"] > 0.10:
                reasons.append(f"ARCH-LM p={r['_arch_lm_p']:.2f} >0.10 (no clustering)")
            if r["_n"] < 504:
                reasons.append(f"only {r['_n']} returns (<504)")
            if r["_backtest"].get("ok") is False:
                reasons.append(f"backtest MAE {r['_backtest'].get('mae_rel')} >0.50")
            if r.get("_forecast_clamped"):
                reasons.append("forecast clamped -- explosive-forecast guard fired")
            print(f"  {r['model_quality'].upper():<18}{r['ticker']:<8} "
                  f"{'; '.join(reasons) or 'see quality rules'}")
    if not failures and not flagged:
        print("  (none -- all tickers reliable)")
    print(bar + "\n")


if __name__ == "__main__":
    arg = 30
    if len(sys.argv) > 1:
        try:
            arg = int(sys.argv[1])
        except ValueError:
            pass
    run(arg)
