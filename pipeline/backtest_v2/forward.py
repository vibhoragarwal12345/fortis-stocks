"""Forward returns and portfolio equity curve for replayed picks.

Entry discipline: a scan published on day D is actionable at the NEXT
session's close at the earliest, so entry = first close after D. This is
slightly conservative vs. intraday execution and removes same-day lookahead.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np
import pandas as pd

from .loader import BENCHMARK, PITPriceLoader

log = logging.getLogger(__name__)

HORIZONS = (1, 5, 20)

# Friction honesty (2026-07-15, absorbed from Vibe-Trading's engine cost
# models): 5 bps slippage per side on the pick leg — a liquid-US retail
# assumption. The benchmark leg stays frictionless (it's a passive yardstick,
# not a trade). Gross numbers are kept alongside for reconciliation.
SLIPPAGE_BPS_PER_SIDE = 5
ROUND_TRIP_COST_PCT = 2 * SLIPPAGE_BPS_PER_SIDE / 100.0   # 0.10%


def forward_returns(loader: PITPriceLoader, picks: list[dict]) -> list[dict]:
    """Attach return_{n}d / alpha_{n}d (SPY-relative, %, net of slippage)
    to each pick; gross values kept under return_{n}d_gross."""
    out = []
    for p in picks:
        row = dict(p)
        for n in HORIZONS:
            entry, exit_ = loader.forward_closes(p["ticker"], p["backtest_date"], n)
            b_entry, b_exit = loader.forward_closes(BENCHMARK, p["backtest_date"], n)
            gross = _pct(exit_, entry)
            bret = _pct(b_exit, b_entry)
            ret = round(gross - ROUND_TRIP_COST_PCT, 4) if gross is not None else None
            row[f"return_{n}d_gross"] = gross
            row[f"return_{n}d"] = ret
            row[f"benchmark_return_{n}d"] = bret
            row[f"alpha_{n}d"] = round(ret - bret, 4) if ret is not None and bret is not None else None
        out.append(row)
    return out


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1) * 100, 4)


def basket_equity_curve(loader: PITPriceLoader, picks: list[dict],
                        hold_days: int = 5) -> pd.Series:
    """Equal-weight rolling basket: each day's top-N entered at next close,
    held `hold_days` sessions. Daily portfolio return = mean of active legs'
    daily returns; equity = cumulative product. Overlapping holds are
    averaged (1/hold_days capital per daily cohort), matching how the focus
    list would actually be traded."""
    spy = loader._panel.get(BENCHMARK)
    if spy is None or not picks:
        return pd.Series(dtype=float)
    cal = spy.index

    daily_legs: dict[pd.Timestamp, list[float]] = defaultdict(list)
    for p in picks:
        df = loader._panel.get(p["ticker"])
        if df is None:
            continue
        fwd = df[df.index > pd.Timestamp(p["backtest_date"])]
        closes = fwd["Close"].iloc[: hold_days + 1]
        rets = closes.pct_change().dropna()
        for ts, r in rets.items():
            if np.isfinite(r):
                daily_legs[ts].append(float(r))

    days = sorted(d for d in daily_legs if d in cal)
    if not days:
        return pd.Series(dtype=float)
    # Round-trip slippage amortized over the holding period of each leg.
    daily_cost = (ROUND_TRIP_COST_PCT / 100.0) / max(hold_days, 1)
    port = pd.Series({d: float(np.mean(daily_legs[d])) - daily_cost
                      for d in days}).sort_index()
    return (1 + port).cumprod() * 100.0  # equity indexed to 100


def churn(picks_by_day: dict[str, list[dict]]) -> dict:
    """Day-over-day focus-list turnover: 1 − Jaccard of consecutive top-N
    sets. High churn = unstable list = advisor whiplash; a strategy change
    that spikes this needs a second look even if alpha improves."""
    days = sorted(picks_by_day)
    if len(days) < 2:
        return {"avg_daily_churn_pct": None, "n_days": len(days)}
    vals = []
    for a, b in zip(days, days[1:]):
        s1 = {p["ticker"] for p in picks_by_day[a]}
        s2 = {p["ticker"] for p in picks_by_day[b]}
        if s1 and s2:
            vals.append(1 - len(s1 & s2) / len(s1 | s2))
    return {
        "avg_daily_churn_pct": round(100 * float(np.mean(vals)), 1) if vals else None,
        "max_daily_churn_pct": round(100 * float(np.max(vals)), 1) if vals else None,
        "n_days": len(days),
    }


def list_correlation(loader: PITPriceLoader, picks_by_day: dict[str, list[dict]],
                     lookback: int = 20) -> dict:
    """Avg pairwise correlation of each day's top-N (trailing daily returns).
    High values mean the 'diversified' list is really one macro trade."""
    daily = []
    for day, picks in sorted(picks_by_day.items()):
        cols = {}
        for p in picks:
            df = loader.asof(p["ticker"], pd.Timestamp(day).date())
            if df is not None and len(df) > lookback + 1:
                cols[p["ticker"]] = df["Close"].pct_change().iloc[-lookback:].values
        if len(cols) < 5:
            continue
        mat = pd.DataFrame(cols).corr().values
        n = mat.shape[0]
        off_diag = (mat.sum() - n) / (n * (n - 1))
        if np.isfinite(off_diag):
            daily.append(off_diag)
    if not daily:
        return {"avg_pairwise_corr": None}
    return {
        "avg_pairwise_corr": round(float(np.mean(daily)), 3),
        "max_day_corr": round(float(np.max(daily)), 3),
        "note": "Above ~0.6 the list behaves like one position, not 35.",
    }


def summarize(picks_with_returns: list[dict]) -> dict:
    """Headline stats per horizon: n, avg/median return + alpha, hit rate."""
    out: dict = {"n_picks": len(picks_with_returns)}
    for n in HORIZONS:
        alphas = [p[f"alpha_{n}d"] for p in picks_with_returns if p.get(f"alpha_{n}d") is not None]
        rets = [p[f"return_{n}d"] for p in picks_with_returns if p.get(f"return_{n}d") is not None]
        if not alphas:
            out[f"h{n}d"] = {"n": 0}
            continue
        arr = np.array(alphas)
        out[f"h{n}d"] = {
            "n": len(arr),
            "avg_return_pct": round(float(np.mean(rets)), 3),
            "avg_alpha_pct": round(float(arr.mean()), 3),
            "median_alpha_pct": round(float(np.median(arr)), 3),
            "beat_benchmark_pct": round(100 * float((arr > 0).mean()), 1),
        }
    return out
