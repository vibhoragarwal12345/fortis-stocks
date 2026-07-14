"""Replay the production funnel as-of a past date.

THE core invariant of backtest v2: the metric and scoring functions are
imported from the live scan modules, not re-implemented. If pipeline/scan
changes, this replay changes identically — reconstruction drift (the v1
failure mode) is structurally impossible.
"""

from __future__ import annotations

import logging
from datetime import date

from scan.layer1_fast_scan import _per_ticker_metrics
from scan.layer2_rank import _score

from .loader import PITPriceLoader

log = logging.getLogger(__name__)

TOP_N = 35  # run_scan.DEFAULT_TOP_N


def replay_funnel(loader: PITPriceLoader, as_of: date,
                  universe: list[str] | None = None,
                  top_n: int = TOP_N) -> list[dict]:
    """Layer 1 (real metrics) + Layer 2 (real score) over PIT data.

    Returns the top-N ranked picks for `as_of`:
    [{ticker, rank, composite_score, metrics…}, …]
    """
    tickers = universe if universe is not None else loader.universe()
    scored: list[tuple[str, float, dict]] = []
    for t in tickers:
        hist = loader.asof(t, as_of)
        if hist is None or len(hist) < 25:
            continue
        metrics = _per_ticker_metrics(hist["Close"], hist["Volume"], hist["Open"])
        if not metrics:
            continue
        s = _score(_layer2_row(metrics))
        if s is None:
            continue
        scored.append((t, s, metrics))

    scored.sort(key=lambda x: x[1], reverse=True)
    out = []
    for rank, (t, s, m) in enumerate(scored[:top_n], start=1):
        out.append({"ticker": t, "rank": rank, "composite_score": s, **m})
    return out


def _layer2_row(metrics: dict) -> dict:
    """Layer-1 metric dict → the scan_results-shaped row _score expects.

    _per_ticker_metrics and the scan_results schema share key names, so this
    is a passthrough; it exists as the single seam to patch if the schema
    ever diverges from the metric keys.
    """
    return metrics
