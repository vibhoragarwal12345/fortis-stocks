"""Factor health / decay classification for the funnel's Layer-1 factors.

Pattern absorbed from HKUDS/Vibe-Trading's strategy_store decay lifecycle
(alive / warning / decayed): daily cross-sectional Spearman IC of each factor
against forward returns, then rolling-vs-baseline comparison. Applied to OUR
five Layer-2 inputs instead of a 461-alpha zoo — same discipline, our factors.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

log = logging.getLogger(__name__)

FACTORS = ("composite_score", "return_20d_pct", "relative_volume",
           "rsi_14", "pct_from_52w_high")

IC_HEALTHY = 0.02      # |mean daily IC| at 5d for a cheap price/volume factor
POS_RATIO_HEALTHY = 0.55
POS_RATIO_WARNING = 0.45


def daily_ic(picks_by_day: dict[str, list[dict]], horizon: int = 5) -> pd.DataFrame:
    """One row per day per factor: cross-sectional Spearman IC vs alpha_{h}d.

    Computed over each day's full replayed top-N (score dispersion within the
    shortlist), answering: does ranking higher inside the list predict better
    forward alpha?"""
    rows = []
    key = f"alpha_{horizon}d"
    for day, picks in sorted(picks_by_day.items()):
        usable = [p for p in picks if p.get(key) is not None]
        if len(usable) < 8:
            continue
        fwd = np.array([p[key] for p in usable], dtype=float)
        for f in FACTORS:
            vals = np.array([_num(p.get(f)) for p in usable], dtype=float)
            mask = np.isfinite(vals)
            if mask.sum() < 8 or np.nanstd(vals[mask]) == 0:
                continue
            ic, _ = spearmanr(vals[mask], fwd[mask])
            if np.isfinite(ic):
                rows.append({"date": day, "factor": f, "ic": round(float(ic), 4)})
    return pd.DataFrame(rows)


def _num(v: Any) -> float:
    if v is None:
        return np.nan
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def classify(ic_df: pd.DataFrame) -> dict[str, dict]:
    """Per-factor health: mean IC, IC-positive ratio, state.

    States: HEALTHY / WARNING / DECAYED / REVERSED / INSUFFICIENT_DATA.
    REVERSED (their alpha-zoo category) = significantly negative mean IC —
    the factor predicts the OPPOSITE of what the weights assume."""
    out: dict[str, dict] = {}
    for f in FACTORS:
        sub = ic_df[ic_df["factor"] == f]["ic"]
        if len(sub) < 5:
            out[f] = {"state": "INSUFFICIENT_DATA", "n_days": int(len(sub))}
            continue
        mean_ic = float(sub.mean())
        pos_ratio = float((sub > 0).mean())
        t_stat = mean_ic / (float(sub.std()) / np.sqrt(len(sub)) + 1e-10)
        if mean_ic <= -IC_HEALTHY and t_stat < -1.5:
            state = "REVERSED"
        elif mean_ic >= IC_HEALTHY and pos_ratio >= POS_RATIO_HEALTHY:
            state = "HEALTHY"
        elif pos_ratio >= POS_RATIO_WARNING:
            state = "WARNING"
        else:
            state = "DECAYED"
        out[f] = {
            "state": state,
            "mean_ic": round(mean_ic, 4),
            "ic_positive_ratio": round(pos_ratio, 3),
            "t_stat": round(float(t_stat), 2),
            "n_days": int(len(sub)),
        }
    return out
