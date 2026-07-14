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


def forward_returns(loader: PITPriceLoader, picks: list[dict]) -> list[dict]:
    """Attach return_{n}d / alpha_{n}d (SPY-relative, %) to each pick."""
    out = []
    for p in picks:
        row = dict(p)
        for n in HORIZONS:
            entry, exit_ = loader.forward_closes(p["ticker"], p["backtest_date"], n)
            b_entry, b_exit = loader.forward_closes(BENCHMARK, p["backtest_date"], n)
            ret = _pct(exit_, entry)
            bret = _pct(b_exit, b_entry)
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
    port = pd.Series({d: float(np.mean(daily_legs[d])) for d in days}).sort_index()
    return (1 + port).cumprod() * 100.0  # equity indexed to 100


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
