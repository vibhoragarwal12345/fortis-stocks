"""Deterministic chart structure: support/resistance + trend slope.

Lean adaptation of HKUDS/Vibe-Trading's pattern_tool (MIT): pure numpy, no
LLM. Only the two detectors with a clear dossier use are kept — clustered
support/resistance levels (complements the Monte Carlo cone: the cone says
how far price wanders, the levels say where it has historically stalled)
and the 20d linear trend slope. The exotic detectors (head-and-shoulders,
triangles, broadening) stay in the clone until they earn an IC through the
factor bench like everything else.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _local_extrema(close: pd.Series, window: int = 5) -> tuple[list[float], list[float]]:
    """Peak/valley prices via rolling-window local extrema."""
    c = close.dropna().values
    peaks, valleys = [], []
    for i in range(window, len(c) - window):
        seg = c[i - window: i + window + 1]
        if c[i] == seg.max():
            peaks.append(float(c[i]))
        elif c[i] == seg.min():
            valleys.append(float(c[i]))
    return peaks, valleys


def _cluster(levels: list[float], tol_pct: float = 1.5) -> list[dict]:
    """Merge nearby levels (within tol_pct) into {level, touches} clusters —
    a level touched three times means more than three one-touch levels."""
    if not levels:
        return []
    levels = sorted(levels)
    clusters: list[list[float]] = [[levels[0]]]
    for v in levels[1:]:
        if (v - clusters[-1][-1]) / clusters[-1][-1] * 100 <= tol_pct:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [{"level": round(float(np.mean(c)), 2), "touches": len(c)}
            for c in clusters]


def support_resistance(close: pd.Series, window: int = 5,
                       max_levels: int = 2) -> dict:
    """Nearest clustered support(s) below and resistance(s) above the last
    close, strongest (most-touched) first within each side."""
    c = close.dropna()
    if len(c) < 60:
        return {}
    last = float(c.iloc[-1])
    peaks, valleys = _local_extrema(c.iloc[-252:], window)
    sup = [cl for cl in _cluster(valleys) if cl["level"] < last]
    res = [cl for cl in _cluster(peaks) if cl["level"] > last]
    sup.sort(key=lambda x: (-x["touches"], last - x["level"]))
    res.sort(key=lambda x: (-x["touches"], x["level"] - last))
    return {
        "supports": sup[:max_levels],
        "resistances": res[:max_levels],
        "method": "clustered_local_extrema_252d",
    }


def trend_slope_20d(close: pd.Series) -> float | None:
    """OLS slope of the last 20 closes, normalized to %/day of latest price."""
    c = close.dropna().iloc[-20:]
    if len(c) < 20 or float(c.iloc[-1]) <= 0:
        return None
    x = np.arange(len(c), dtype=float)
    slope = float(np.polyfit(x, c.values.astype(float), 1)[0])
    return round(slope / float(c.iloc[-1]) * 100, 3)
