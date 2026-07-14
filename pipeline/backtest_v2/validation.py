"""Statistical validation: Monte Carlo permutation, bootstrap Sharpe CI,
walk-forward consistency.

Vendored and adapted from HKUDS/Vibe-Trading `agent/backtest/validation.py`
(MIT License, Copyright (c) 2026 HKUDS — https://github.com/HKUDS/Vibe-Trading),
at commit e88db0f. Adaptations: our unit of account is a *pick* with a
forward alpha (%), not a round-trip trade with PnL; equity curves come from
forward.basket_equity_curve.
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd


def monte_carlo_test(alphas: List[float], n_simulations: int = 1000,
                     seed: int = 42) -> Dict[str, Any]:
    """Shuffle pick-alpha order to test path significance of the sequence.

    Null hypothesis: the realized Sharpe / max drawdown of the pick sequence
    is no better than a random ordering of the same alphas."""
    if len(alphas) < 3:
        return {"error": "need at least 3 picks", "p_value_sharpe": 1.0}

    pnls = np.array(alphas, dtype=float)
    actual = _path_metrics(pnls)
    rng = np.random.default_rng(seed)
    sharpe_count = dd_count = 0
    sim_sharpes = []
    for _ in range(n_simulations):
        sim = _path_metrics(rng.permutation(pnls))
        sim_sharpes.append(sim["sharpe"])
        if sim["sharpe"] >= actual["sharpe"]:
            sharpe_count += 1
        if sim["max_dd"] >= actual["max_dd"]:
            dd_count += 1
    arr = np.array(sim_sharpes)
    return {
        "actual_sharpe": round(actual["sharpe"], 4),
        "actual_max_dd": round(actual["max_dd"], 4),
        "p_value_sharpe": round(sharpe_count / n_simulations, 4),
        "p_value_max_dd": round(dd_count / n_simulations, 4),
        "simulated_sharpe_mean": round(float(arr.mean()), 4),
        "simulated_sharpe_p95": round(float(np.percentile(arr, 95)), 4),
        "n_simulations": n_simulations,
        "n_picks": len(pnls),
    }


def _path_metrics(pnls: np.ndarray, initial_capital: float = 100.0) -> Dict[str, float]:
    equity = initial_capital + np.cumsum(pnls)
    returns = np.diff(equity) / equity[:-1] if len(equity) > 1 else np.array([0.0])
    std = returns.std()
    sharpe = float(returns.mean() / (std + 1e-10) * np.sqrt(252))
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak > 0, peak, 1.0)
    return {"sharpe": sharpe, "max_dd": float(dd.min())}


def bootstrap_sharpe_ci(equity_curve: pd.Series, n_bootstrap: int = 1000,
                        confidence: float = 0.95, bars_per_year: int = 252,
                        seed: int = 42) -> Dict[str, Any]:
    """Resample daily returns to estimate the Sharpe confidence interval."""
    returns = equity_curve.pct_change().dropna().values
    if len(returns) < 5:
        return {"error": "need at least 5 return observations"}
    observed = _sharpe(returns, bars_per_year)
    rng = np.random.default_rng(seed)
    boot = [_sharpe(rng.choice(returns, size=len(returns), replace=True), bars_per_year)
            for _ in range(n_bootstrap)]
    arr = np.array(boot)
    alpha = (1 - confidence) / 2
    return {
        "observed_sharpe": round(observed, 4),
        "ci_lower": round(float(np.percentile(arr, alpha * 100)), 4),
        "ci_upper": round(float(np.percentile(arr, (1 - alpha) * 100)), 4),
        "median_sharpe": round(float(np.median(arr)), 4),
        "prob_positive": round(float(np.mean(arr > 0)), 4),
        "confidence": confidence,
        "n_bootstrap": n_bootstrap,
    }


def _sharpe(returns: np.ndarray, bars_per_year: int = 252) -> float:
    std = returns.std()
    return float(returns.mean() / (std + 1e-10) * np.sqrt(bars_per_year))


def walk_forward(equity_curve: pd.Series, n_windows: int = 4,
                 bars_per_year: int = 252) -> Dict[str, Any]:
    """Split the equity curve into sequential windows; check consistency."""
    if len(equity_curve) < n_windows * 2:
        return {"error": f"need at least {n_windows * 2} bars for {n_windows} windows"}
    size = len(equity_curve) // n_windows
    windows = []
    for i in range(n_windows):
        seg = equity_curve.iloc[i * size: (i + 1) * size if i < n_windows - 1 else len(equity_curve)]
        rets = seg.pct_change().dropna().values
        if len(rets) == 0:
            continue
        windows.append({
            "window": i + 1,
            "start": str(seg.index[0].date()),
            "end": str(seg.index[-1].date()),
            "return_pct": round(float(seg.iloc[-1] / seg.iloc[0] - 1) * 100, 3),
            "sharpe": round(_sharpe(rets, bars_per_year), 3),
        })
    pos = sum(1 for w in windows if w["return_pct"] > 0)
    return {
        "per_window": windows,
        "positive_windows": f"{pos}/{len(windows)}",
        "sharpe_std_across_windows": round(float(np.std([w["sharpe"] for w in windows])), 3)
        if windows else None,
    }
