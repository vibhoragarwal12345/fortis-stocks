"""Backtest v2 orchestrator.

    python -m pipeline.backtest_v2.run_backtest --start 2026-06-18 --end 2026-07-15

Read-only against Supabase (live universe + focus lists for fidelity); all
outputs are local files under pipeline/reports/backtest_v2/. Nothing here
touches production tables or the dashboard.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

from backtest_v2 import decay, fidelity, forward, validation  # noqa: E402
from backtest_v2.loader import PITPriceLoader  # noqa: E402
from backtest_v2.replay import TOP_N, replay_funnel  # noqa: E402
from backtest_v2.run_card import write_run_card  # noqa: E402
from scan.layer1_fast_scan import load_universe  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("backtest_v2")


def trading_days(start: date, end: date) -> list[date]:
    days, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--top-n", type=int, default=TOP_N)
    ap.add_argument("--sample", type=int, default=None,
                    help="Restrict universe to first N tickers (dev only)")
    args = ap.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # ── Universe: PIT per-day where a live scan exists, else current CSV ──
    days = trading_days(start, end)
    day_universe: dict[str, list[str] | None] = {}
    for d in days:
        day_universe[d.isoformat()] = fidelity.live_universe_for_day(db, d.isoformat())
    pit_days = sum(1 for v in day_universe.values() if v)
    fallback = load_universe(sample=args.sample)
    all_tickers = set(fallback)
    for v in day_universe.values():
        if v:
            all_tickers.update(v)
    provenance = (f"scan_results PIT for {pit_days}/{len(days)} days, "
                  f"full_universe.csv fallback otherwise")
    log.info("Universe: %d tickers (%s)", len(all_tickers), provenance)

    # ── PIT panel (one download, cached) ──────────────────────────────────
    loader = PITPriceLoader(sorted(all_tickers), start, end,
                            cache_key=f"{start}_{end}_{len(all_tickers)}")
    loader.load()
    assert loader.lookahead_self_test(start - timedelta(days=1)), \
        "lookahead self-test FAILED — aborting"
    log.info("lookahead self-test passed")

    # ── Replay funnel per day ─────────────────────────────────────────────
    picks_by_day: dict[str, list[dict]] = {}
    for d in days:
        key = d.isoformat()
        uni = day_universe[key]
        picks = replay_funnel(loader, d, universe=uni, top_n=args.top_n)
        for p in picks:
            p["backtest_date"] = key
        picks_by_day[key] = picks
        log.info("%s: replayed top-%d (universe=%s)", key, len(picks),
                 "PIT" if uni else "csv")

    all_picks = [p for ps in picks_by_day.values() for p in ps]
    if not all_picks:
        log.error("no picks replayed — aborting")
        return 1

    # ── Forward returns + summary ─────────────────────────────────────────
    all_picks = forward.forward_returns(loader, all_picks)
    picks_by_day = {}
    for p in all_picks:
        picks_by_day.setdefault(p["backtest_date"], []).append(p)
    summary = forward.summarize(all_picks)
    summary["slippage_bps_per_side"] = forward.SLIPPAGE_BPS_PER_SIDE
    summary["churn"] = forward.churn(picks_by_day)
    summary["list_correlation"] = forward.list_correlation(loader, picks_by_day)

    # ── Fidelity vs live ──────────────────────────────────────────────────
    live = fidelity.live_focus_lists(db, args.start, args.end)
    fid = fidelity.fidelity_report(picks_by_day, live)

    # ── Validation ────────────────────────────────────────────────────────
    equity = forward.basket_equity_curve(loader, all_picks, hold_days=5)
    alphas_5d = [p["alpha_5d"] for p in all_picks if p.get("alpha_5d") is not None]
    val = {
        "monte_carlo": validation.monte_carlo_test(alphas_5d),
        "bootstrap": (validation.bootstrap_sharpe_ci(equity)
                      if len(equity) else {"error": "no equity curve"}),
        "walk_forward": (validation.walk_forward(equity)
                         if len(equity) else {"error": "no equity curve"}),
    }

    # ── Factor health ─────────────────────────────────────────────────────
    ic_df = decay.daily_ic(picks_by_day, horizon=5)
    health = decay.classify(ic_df)

    # ── Run card ──────────────────────────────────────────────────────────
    matured_20d = summary.get("h20d", {}).get("n", 0)
    caveats = [
        "Entry = next session close after scan date (conservative; no same-day fill).",
        "Replay uses complete daily bars; live intraday scans see partial-day bars, "
        "so intraday-scan selections can differ legitimately.",
        f"Universe provenance: {provenance}. CSV-fallback days carry mild "
        "survivorship risk (current universe applied to past dates).",
        "IC computed within the daily top-N shortlist (rank dispersion), not the "
        "full market cross-section.",
    ]
    if matured_20d < 30:
        caveats.append(f"Only {matured_20d} picks have matured 20d returns — "
                       "treat 20d numbers as preliminary until n >= 30.")
    cfg = {"start": args.start, "end": args.end, "top_n": args.top_n,
           "hold_days": 5, "benchmark": "SPY",
           "universe_provenance": provenance,
           "universe_size": len(all_tickers), "engine": "backtest_v2/replay"}
    path = write_run_card(cfg, summary, fid, val, health, caveats)
    log.info("run card -> %s (and .md)", path)
    print(f"\nRun card: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
