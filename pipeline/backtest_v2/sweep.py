"""Weight sweep — test candidate Layer-2 strategies fast, decide on evidence.

Replaces the retired blanket 90-day rule (removed 2026-07-15 by owner
decision). Speed comes from reusing the cached PIT panel: layer-1 metrics
are computed once per (day, ticker), then every candidate weight vector is
re-scored through the REAL production `_score` in memory. A full grid over
a month's window runs in minutes.

The calendar gate is gone; the evidence gates that replace it (enforced in
the leaderboard, not by waiting):
  - Monte Carlo permutation p(Sharpe) — is the ordering better than luck?
  - split-half consistency — candidate must beat baseline in BOTH halves
    of the window, not just on average.
  - the run card records sample size and window so small-n results are
    labeled, not hidden.

    python -m pipeline.backtest_v2.sweep --start 2026-06-18 --end 2026-07-15
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

from backtest_v2 import fidelity, validation  # noqa: E402
from backtest_v2.loader import BENCHMARK, PITPriceLoader  # noqa: E402
from backtest_v2.run_card import REPORTS_DIR, _git_sha  # noqa: E402
from backtest_v2.run_backtest import trading_days  # noqa: E402
from scan.layer1_fast_scan import _per_ticker_metrics, load_universe  # noqa: E402
from scan.layer2_rank import WEIGHTS as PROD_WEIGHTS  # noqa: E402
from scan.layer2_rank import _score  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("backtest_v2.sweep")

TOP_N = 35

# Grid: one factor scaled at a time plus targeted combos, all renormalized
# to sum to 1. Coordinate sweep (like Vibe-Trading's optimizer) — the full
# cartesian product explodes and invites overfitting.
SCALES = (0.0, 0.4, 0.7, 1.0, 1.3, 1.6, 2.0)


def candidate_grid() -> list[dict[str, float]]:
    cands: list[dict[str, float]] = []
    seen: set[tuple] = set()

    def _add(w: dict[str, float]) -> None:
        total = sum(w.values())
        if total <= 0:
            return
        norm = {k: round(v / total, 4) for k, v in w.items()}
        key = tuple(sorted(norm.items()))
        if key not in seen:
            seen.add(key)
            cands.append(norm)

    _add(dict(PROD_WEIGHTS))                       # baseline first
    for factor in PROD_WEIGHTS:
        for s in SCALES:
            w = dict(PROD_WEIGHTS)
            w[factor] = PROD_WEIGHTS[factor] * s
            _add(w)
    # Targeted combos from the factor-health finding: momentum down AND
    # volume up together, in a few strengths.
    for ms, vs in itertools.product((0.0, 0.4, 0.7), (1.3, 1.6, 2.0)):
        w = dict(PROD_WEIGHTS)
        w["momentum"] = PROD_WEIGHTS["momentum"] * ms
        w["volume"] = PROD_WEIGHTS["volume"] * vs
        _add(w)
    return cands


def precompute(loader: PITPriceLoader, days: list[date],
               day_universe: dict[str, list[str] | None]) -> dict[str, list[dict]]:
    """Layer-1 metrics + 5d forward alpha per (day, ticker), computed once."""
    out: dict[str, list[dict]] = {}
    for d in days:
        key = d.isoformat()
        tickers = day_universe.get(key) or loader.universe()
        b_entry, b_exit = loader.forward_closes(BENCHMARK, key, 5)
        bret = (b_exit / b_entry - 1) * 100 if b_entry and b_exit else None
        rows = []
        for t in tickers:
            hist = loader.asof(t, d)
            if hist is None or len(hist) < 25:
                continue
            m = _per_ticker_metrics(hist["Close"], hist["Volume"], hist["Open"])
            if not m:
                continue
            m["ticker"] = t
            if bret is not None:
                e, x = loader.forward_closes(t, key, 5)
                m["alpha_5d"] = ((x / e - 1) * 100 - bret) if e and x else None
            else:
                m["alpha_5d"] = None
            rows.append(m)
        out[key] = rows
        log.info("%s: %d tickers precomputed", key, len(rows))
    return out


def evaluate(weights: dict[str, float], panel: dict[str, list[dict]],
             top_n: int = TOP_N) -> dict:
    """Score every day with `weights` via the production _score, take top-N,
    aggregate 5d alpha. Chronological pick order for the MC test."""
    alphas: list[float] = []
    daily_means: list[tuple[str, float]] = []
    for day in sorted(panel):
        rows = panel[day]
        scored = [(r, _score(r, weights)) for r in rows]
        scored = [(r, s) for r, s in scored if s is not None]
        scored.sort(key=lambda x: x[1], reverse=True)
        day_alphas = [r["alpha_5d"] for r, _ in scored[:top_n]
                      if r.get("alpha_5d") is not None]
        alphas.extend(day_alphas)
        if day_alphas:
            daily_means.append((day, float(np.mean(day_alphas))))
    if not alphas:
        return {"n": 0}
    arr = np.array(alphas)
    halves = np.array_split([m for _, m in daily_means], 2)
    return {
        "n": len(arr),
        "avg_alpha_5d": round(float(arr.mean()), 3),
        "median_alpha_5d": round(float(np.median(arr)), 3),
        "hit_rate": round(100 * float((arr > 0).mean()), 1),
        "first_half_avg": round(float(np.mean(halves[0])), 3) if len(halves[0]) else None,
        "second_half_avg": round(float(np.mean(halves[1])), 3) if len(halves[1]) else None,
        "mc_p_sharpe": validation.monte_carlo_test(list(arr))
                        .get("p_value_sharpe"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--top-n", type=int, default=TOP_N)
    args = ap.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    days = trading_days(start, end)
    day_universe = {d.isoformat(): fidelity.live_universe_for_day(db, d.isoformat())
                    for d in days}
    all_tickers = set(load_universe())
    for v in day_universe.values():
        if v:
            all_tickers.update(v)

    loader = PITPriceLoader(sorted(all_tickers), start, end,
                            cache_key=f"{start}_{end}_{len(all_tickers)}")
    loader.load()
    panel = precompute(loader, days, day_universe)

    results = []
    for w in candidate_grid():
        r = evaluate(w, panel, top_n=args.top_n)
        if r.get("n"):
            results.append({"weights": w, **r})
    baseline = results[0]  # grid emits baseline first
    for r in results:
        r["delta_vs_baseline"] = round(r["avg_alpha_5d"] - baseline["avg_alpha_5d"], 3)
        r["beats_baseline_both_halves"] = (
            r["first_half_avg"] is not None and baseline["first_half_avg"] is not None
            and r["first_half_avg"] > baseline["first_half_avg"]
            and r["second_half_avg"] > baseline["second_half_avg"]
        )
    results.sort(key=lambda r: r["avg_alpha_5d"], reverse=True)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    out_path = REPORTS_DIR / f"sweep_{stamp}.json"
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_git_sha": _git_sha(),
        "window": {"start": args.start, "end": args.end},
        "top_n": args.top_n,
        "baseline": baseline,
        "leaderboard": results[:20],
        "evidence_gates": [
            "mc_p_sharpe <= 0.10 before acting on a candidate",
            "beats_baseline_both_halves must be true (split-half consistency)",
            "re-run on a second window before production adoption",
        ],
    }, indent=2), encoding="utf-8")

    print(f"\n{'rank':<5}{'avg_a5d':>9}{'hit%':>7}{'p(MC)':>8}{'both½':>7}  weights")
    for i, r in enumerate(results[:12], 1):
        wstr = " ".join(f"{k[:3]}={v:.2f}" for k, v in r["weights"].items())
        print(f"{i:<5}{r['avg_alpha_5d']:>9.2f}{r['hit_rate']:>7.1f}"
              f"{str(r['mc_p_sharpe']):>8}{str(r['beats_baseline_both_halves']):>7}  {wstr}")
    b = baseline
    print(f"\nbaseline: avg_a5d={b['avg_alpha_5d']} hit={b['hit_rate']}% (n={b['n']})")
    print(f"leaderboard -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
