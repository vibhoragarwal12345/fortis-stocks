# Backtest Engine v2 — Rebuild Plan

**Date:** 2026-07-15 · **Branch:** `backtest-v2/absorb-vibe` · **Status:** Phase 1 implemented

## Why rebuild

The 2026-07-15 backtest run (window 2026-06-18 → 2026-07-15, 600 hypothetical picks)
proved that `pipeline/agents/backtest_engine.py` replays the **legacy**
`ranking_engine.py` signal-store strategy, while production picks have come from the
`pipeline/scan/` two-speed funnel since the May 2026 lean rewrite. Overlap between
reconstruction and live picks: **~2%**. The old engine validates a strategy we no
longer run.

Secondary defects found the same day:
- `generate_performance_report` queries `macro_context.snapshot_date`; the column is
  `snapshot_time` → silent HTTP 400, the regime section has been missing from every
  monthly report.
- The monthly report file is written to the ephemeral Actions runner and never
  persisted (no artifact upload in `weekly_rollup.yml`).

## Design principles (absorbed from HKUDS/Vibe-Trading, MIT)

Studied at commit `e88db0f` (2026-07-14). What we take and why:

1. **Replay the real code, not a copy of it.** The single root cause of the v1
   failure was a re-implementation drifting from production. v2 **imports**
   `layer1_fast_scan._per_ticker_metrics` and `layer2_rank._score` directly. If the
   product changes, the backtest changes with it, by construction.
2. **PIT safety at the loader boundary** (their `AsOfSignalStore` equivalent lives in
   loaders, not queries). One `PITPriceLoader` downloads the full OHLCV panel once,
   sanity-checks bars centrally (high ≥ low, positive prices), and serves truncated
   `≤ as_of` views. Nothing downstream can see the future.
3. **Run cards** (their `run_card.py`): every run emits a JSON + markdown artifact
   with code git SHA, config hash, window, universe provenance, metrics, validation
   results, and an explicit caveats section. No naked numbers.
4. **Statistical validation** (vendored from their `backtest/validation.py`, MIT,
   attribution in file header): Monte Carlo permutation test (is the pick ordering
   luck?), bootstrap Sharpe CI (how uncertain is the Sharpe?), walk-forward windows
   (is performance consistent through time?).
5. **Factor decay lifecycle** (their `strategy_store/decay.py` pattern): daily
   cross-sectional IC of each Layer-1 factor vs forward returns, rolling-vs-baseline
   ratio, HEALTHY → WARNING → DECAYED classification per factor. This is the
   alpha-zoo "alive/reversed/dead" idea applied to *our* five factors.
6. **Fidelity gate** (our addition; their swarm has no live product to compare to):
   replayed top-N vs the live `ranked_focus_list` per day. This is the "does live
   match the recipe" test the old engine failed. Premarket/postclose scans replay
   exactly (complete daily bars); intraday scans use partial-day bars, so fidelity is
   measured against the closest complete-bar scan and the difference is disclosed.

## Architecture

```
pipeline/backtest_v2/
  loader.py       PITPriceLoader — one yfinance panel download, parquet cache,
                  OHLC sanity checks, .asof(ticker, date) truncated views
  replay.py       replay_funnel(date) — real layer1 metrics + real layer2 score
                  over the PIT panel → top-N picks per day
  forward.py      forward 1d/5d/20d returns + SPY alpha per pick; equal-weight
                  5d-hold rolling basket → daily portfolio equity curve
  validation.py   VENDORED (MIT, HKUDS/Vibe-Trading) MC permutation, bootstrap
                  Sharpe CI, walk-forward — adapted: picks-as-trades
  fidelity.py     replayed top-N ∩ live ranked_focus_list per day + metric-level
                  diffs vs stored scan_results
  decay.py        per-factor daily Spearman IC vs fwd returns, rolling/baseline
                  ratio, decay classification (lean state machine)
  run_card.py     JSON + markdown run card under pipeline/reports/backtest_v2/
  run_backtest.py CLI orchestrator: replay → forward → fidelity → validation →
                  decay → run card
```

**Universe provenance (PIT):** for days where a live scan ran, the replay universe is
that day's actual `scan_results` tickers (true point-in-time universe). For earlier
days it falls back to the current `full_universe.csv` with a
`universe_provenance: "current_csv"` flag in the run card (mild survivorship risk,
disclosed, acceptable for pre-launch windows).

**Zero production impact:** v2 reads Supabase, never writes it. All artifacts are
local files. No `src/` (web) changes. No schema changes. The dashboard cannot see any
of this until we intentionally wire it in post-validation.

## Phases

- **Phase 1 (this branch):** package above + old-engine bug fixes
  (`snapshot_time` column, report artifact upload) + first validated run over
  2026-06-18 → 2026-07-15 with fidelity report.
- **Phase 2:** nightly decay scan as a workflow step; factor-health section in the
  weekly rollup; `backtest_runs` table (new migration) once outputs stabilize.
- **Phase 3 (per the Vibe-Trading absorption roadmap):** hypothesis registry
  (hypothesis → config → run → evidence-back), weight/threshold sweeps for Layer-2
  under walk-forward guard (≥90 days forward data before any production weight
  change — carried over from v1 spec), Shadow-Account-style "you vs the system"
  comparisons as a product feature.

## What happens to v1

`backtest_engine.py` stays (its `generate_performance_report` still serves the
monthly track-record report — live `pick_outcomes` data, not reconstruction — and
gets the column fix here). Its `historical_backtest`/`weight_optimization` are
deprecated for decision-making; `backtest_picks` rows from 2026-07-15 are legacy-
strategy reconstructions and must not be mixed with v2 outputs.
