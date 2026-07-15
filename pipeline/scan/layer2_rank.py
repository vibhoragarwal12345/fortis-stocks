"""
Layer 2 -- Rank & Shortlist
============================

Reads the layer-1 scan_results for the given scan_id, computes a cheap
composite score per ticker, picks the top N, and writes them to
ranked_focus_list with the same scan_id stamped on each row so layer-3
agents can pick them up as the day's focus list.

COMPOSITE SCORE (0-100) — weights adopted 2026-07-15 via backtest_v2 sweeps
  momentum       0.00   -- 20d return; ZEROED (REVERSED IC within shortlist)
  volume         0.33   -- relative_volume above 1.0 is good
  breakout       0.21   -- binary, 100 if is_breakout
  proximity      0.15   -- closer to 52w high = better
  rsi            0.15   -- sweet spot 50-70; oversold <30 = bonus
  low_proximity  0.15   -- pct-rank of min(close,20d)/close (alpha bench)
  (cap)                 -- hard cap if 52w stats missing (recent IPO)
  (see WEIGHTS below for evidence + prior values)

CLI
  python -m pipeline.scan.layer2_rank --scan-id 123
  python -m pipeline.scan.layer2_rank --scan-id 123 --top-n 80
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _clip(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


# Production composite weights. _score(row) with no weights argument uses
# exactly these; backtest_v2 weight sweeps pass candidate weights through
# this same function so experiments can never diverge from what production
# would compute with those weights.
#
# ADOPTED 2026-07-15 (owner decision) from the backtest_v2 sweep, replacing
# the launch weights (mom .25 / vol .25 / br .20 / px .15 / rsi .15).
# Evidence: momentum-zero configs won BOTH test windows independently —
# in-sample Jun 18–Jul 15 (avg 5d alpha −0.93% vs baseline −1.97%) and
# out-of-sample May 19–Jun 17 (+0.11% vs −0.88%), split-half consistent in
# both; 20d momentum had REVERSED IC (−0.22) within the shortlist. This is
# the best JOINT performer across windows, not either window's champion.
# Sweep artifacts: pipeline/reports/backtest_v2/sweep_2026-07-14_*.json.
# Picks from this date forward are not directly comparable to earlier
# track-record rows — the strategy changed here.
WEIGHTS = {
    "momentum":  0.0,
    "volume":    0.3348,
    "breakout":  0.2061,
    "proximity": 0.1545,
    "rsi":       0.1545,
    # Alpha-bench factors (2026-07-15, two-window factor bench in
    # backtest_v2/factor_bench.py). low_proximity ADOPTED same day via the
    # extended sweep: beat the momentum-zero champion in BOTH windows
    # (IS −0.17 vs −0.93; OOS +0.42 vs +0.11 avg 5d alpha) with lower
    # half-to-half variance. quiet_volume / calm_tape stay wired at 0.0 —
    # measured nightly, activated only if a future sweep earns them weight.
    "quiet_volume":  0.0,   # 1 − pct-rank of volume_vol_20d   (bench IC −0.079)
    "low_proximity": 0.15,  # pct-rank of low20_ratio          (bench IC +0.077)
    "calm_tape":     0.0,   # 1 − pct-rank of pv_corr_20d      (bench IC −0.075)
}

# Cross-sectional factors: scored as percentile ranks within the day's scan
# (matching how their IC was measured — Spearman is rank-based). Sign −1
# means LOW raw value earns a HIGH score.
CROSS_SECTIONAL = {
    "quiet_volume":  ("volume_vol_20d", -1),
    "low_proximity": ("low20_ratio",    +1),
    "calm_tape":     ("pv_corr_20d",    -1),
}


def add_cross_sectional_scores(rows: list[dict]) -> None:
    """Attach <component>_pts (0-100 percentile scores) in place.

    Called by rank_and_persist on the full scan cross-section, and by
    backtest_v2 replay/sweep on each day's replayed cross-section — the
    same code path in both, so backtests can't diverge from production.
    Rows missing a raw value get a neutral 50.
    """
    for comp, (raw_key, sign) in CROSS_SECTIONAL.items():
        vals = [(i, r[raw_key]) for i, r in enumerate(rows)
                if r.get(raw_key) is not None]
        for r in rows:
            r[f"{comp}_pts"] = 50.0
        if len(vals) < 30:
            continue
        vals.sort(key=lambda t: t[1])
        n = len(vals) - 1 or 1
        for rank_pos, (i, _) in enumerate(vals):
            pct = rank_pos / n
            rows[i][f"{comp}_pts"] = round(100 * (pct if sign > 0 else 1 - pct), 2)


def _score(row: dict, weights: dict[str, float] = WEIGHTS) -> float | None:
    """Composite 0-100. Returns None if the row is too thin to score."""
    mom = row.get("return_20d_pct")
    rv  = row.get("relative_volume")
    px  = row.get("pct_from_52w_high")
    rsi = row.get("rsi_14")
    if mom is None and rv is None and rsi is None:
        return None

    # Momentum -- arctan-ish mapping so big moves don't dominate.
    mom_pts = 0.0
    if mom is not None:
        # 0% -> 50, +25% -> ~85, -25% -> ~15
        mom_pts = _clip(50 + 35 * math.tanh(float(mom) / 25))

    # Relative volume -- 1.0 = 50, 2.0 = 75, 5.0 = 95+
    vol_pts = 50.0
    if rv is not None:
        v = float(rv)
        vol_pts = _clip(40 + 40 * math.tanh((v - 1) / 1.5))

    # Breakout -- binary boost
    br_pts = 100.0 if row.get("is_breakout") else (
        20.0 if row.get("is_breakdown") else 50.0
    )

    # 52w proximity -- 0 = at high, -50% = 0 pts, +5% (new high) = 100
    px_pts = 50.0
    if px is not None:
        p = float(px)
        # pct_from_52w_high is <= 0 typically (or slightly +ve for new highs).
        # Map -50 -> 0, 0 -> 80, +5 -> 100.
        px_pts = _clip(80 + p * 4)

    # RSI -- sweet spot 50-70 ~= 80 pts; 30 = 60 (oversold bounce); 80+ = 30
    rsi_pts = 50.0
    if rsi is not None:
        r = float(rsi)
        if r < 30:
            rsi_pts = 60 + (30 - r)  # oversold bounce bonus
        elif r <= 70:
            rsi_pts = 60 + (r - 30)  # rising toward 70 = stronger
        else:
            rsi_pts = max(20, 100 - (r - 70) * 2)  # >70 starts to decay
        rsi_pts = _clip(rsi_pts)

    composite = (
        weights["momentum"] * mom_pts
        + weights["volume"] * vol_pts
        + weights["breakout"] * br_pts
        + weights["proximity"] * px_pts
        + weights["rsi"] * rsi_pts
    )
    # Cross-sectional components (percentile pts from
    # add_cross_sectional_scores; neutral 50 when absent).
    for comp in CROSS_SECTIONAL:
        w = weights.get(comp, 0.0)
        if w:
            composite += w * float(row.get(f"{comp}_pts", 50.0))
    return round(composite, 2)


def rank_and_persist(scan_id: int, top_n: int = 80) -> int:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Pull every scan_results row for this scan. With 3-5k tickers it
    # fits in one paginated select.
    rows: list[dict] = []
    start = 0
    page = 1000
    while True:
        chunk = (
            db.table("scan_results")
              .select("*")
              .eq("scan_id", scan_id)
              .range(start, start + page - 1)
              .execute()
              .data or []
        )
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < page:
            break
        start += page
    if not rows:
        log.warning("No scan_results for scan_id=%d", scan_id)
        return 0
    log.info("Layer 2: scoring %d scan_results rows", len(rows))

    add_cross_sectional_scores(rows)
    scored: list[tuple[str, float, dict]] = []
    for r in rows:
        s = _score(r)
        if s is None:
            continue
        scored.append((r["ticker"], s, r))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:top_n]
    log.info(
        "Top %d composites: %s",
        min(10, len(top)),
        ", ".join(f"{t}={s:.0f}" for t, s, _ in top[:10]),
    )

    # 1. Update scan_results: composite_score + rank + advanced flag for all.
    updates = []
    for rank, (ticker, score, _r) in enumerate(scored, start=1):
        updates.append({
            "scan_id": scan_id,
            "ticker":  ticker,
            "composite_score": score,
            "rank":            rank,
            "advanced":        rank <= top_n,
        })
    # supabase-py doesn't expose a bulk-update; use upsert on the unique key.
    for i in range(0, len(updates), 500):
        db.table("scan_results").upsert(
            updates[i : i + 500], on_conflict="scan_id,ticker"
        ).execute()
    log.info("Updated %d scan_results with score + rank + advanced", len(updates))

    # 2. Mirror the top N into ranked_focus_list so the existing layer-3
    #    agents (catalyst_agent, smart_money_intel, etc.) read them as
    #    today's focus list.
    #
    #    We deliberately stamp run_type='midday'. The legacy daily
    #    pipeline (premarket/midday/close) is gone; reusing 'midday'
    #    lets the unmodified deep agents pick the list up. scan_id is
    #    the real lineage key -- multiple scans per day upsert into the
    #    same (run_date, 'midday', ticker) row, with the latest scan
    #    winning. The dashboard reads market_scans + scan_results, both
    #    keyed on scan_id, so users always see the latest scan
    #    unambiguously.
    today = date.today().isoformat()
    focus_rows = []
    for rank, (ticker, score, _r) in enumerate(top, start=1):
        focus_rows.append({
            "ticker":          ticker,
            "run_date":        today,
            "run_type":        "midday",
            "rank":            rank,
            "composite_score": score,
            "scan_id":         scan_id,
        })
    for i in range(0, len(focus_rows), 200):
        db.table("ranked_focus_list").upsert(
            focus_rows[i : i + 200],
            on_conflict="run_date,run_type,ticker",
        ).execute()
    log.info("Wrote %d ranked_focus_list rows (run_type='midday')", len(focus_rows))
    return len(top)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-id", type=int, required=True)
    ap.add_argument("--top-n",   type=int, default=80)
    args = ap.parse_args()
    rank_and_persist(scan_id=args.scan_id, top_n=args.top_n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
