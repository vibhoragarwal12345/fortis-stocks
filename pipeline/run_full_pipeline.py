"""
Full Pipeline Re-Run Orchestrator
Runs every agent end-to-end in strict dependency order. Each agent runs as an
isolated subprocess (so memory and module state never collide -- pattern_matcher
alone mmaps a 3 GB FAISS index). A failing step is logged and the run continues;
downstream agents all degrade gracefully on missing inputs.

Dependency order:
  1  market_data        -> market_snapshots (+ technical jsonb columns)
  2  crowd_intelligence -> ticker_debates
  3  sentiment_scorer   -> news_items scores + ticker_sentiment_rollup
  4  anomaly_detector   -> anomaly_flags        (needs 1: technicals)
  5  pattern_matcher    -> pattern_matches      (local FAISS index)
  6  cross_validator    -> validated_signals    (needs 3,4 + crowd)
  7  ranking_engine     -> ranked_focus_list    (needs 5,6 + raw signals)
  8  catalyst_agent     -> ranked_focus_list catalyst_* (needs 7; uses prior-day
                          smart_money_intel as override when available)
  9  debate_synthesizer -> ranked_focus_list bull/bear (needs 7)
  10 critic_agent       -> ranked_focus_list critic_*  (needs 9: bull_case;
                          uses prior-day smart_money_intel for objection caps)
  11 smart_money_intel  -> smart_money_intel    (needs 7; Step 6.7 -- four
                          institutional signals + pattern + intel note. Slotted
                          here per spec; downstream agents (catalyst/debate/
                          critic) pick up today's row on the NEXT pipeline run)
  12 quant desk MC+VaR  -> monte_carlo_results, *_risk_metrics (needs 7)
  13 GARCH desk         -> garch_forecasts      (needs 7)

Usage:
    python pipeline/run_full_pipeline.py
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # repo root
PY = sys.executable
STEP_TIMEOUT = 5400                                     # 90 min hard cap per step

STEPS = [
    ("market_data (full universe -- technicals)", "pipeline/agents/market_data.py", []),
    ("crowd_intelligence",          "pipeline/agents/crowd_intelligence.py", ["midday"]),
    ("sentiment_scorer",            "pipeline/processors/sentiment_scorer.py", ["midday"]),
    ("anomaly_detector",            "pipeline/agents/anomaly_detector.py", ["midday"]),
    ("pattern_matcher",             "pipeline/agents/pattern_matcher.py", []),
    ("cross_validator",             "pipeline/agents/cross_validator.py", ["midday"]),
    ("ranking_engine (all 3 runs)", "pipeline/agents/ranking_engine.py", []),
    ("catalyst_agent",              "pipeline/agents/catalyst_agent.py", []),
    ("debate_synthesizer (top 30)", "pipeline/agents/debate_synthesizer.py", ["30"]),
    ("critic_agent (top 30)",       "pipeline/agents/critic_agent.py", ["30"]),
    # ── Step 6.7 -- four institutional signals + pattern synthesis + intel note.
    # Per spec, slotted between critic_agent and quant desk. Today's row is
    # picked up by catalyst/debate/critic on tomorrow's pipeline run.
    ("smart_money_intel (midday top 30)", "pipeline/agents/smart_money_intel.py", ["midday", "30"]),
    # Position management was removed alongside portfolios. The quant desk
    # aggregator (run_desk.py) is being rebuilt per-ticker in PART 3 of the
    # lean rewrite; until then we skip it here.
    ("GARCH desk (top 30)",         "pipeline/quant/run_garch.py", ["30"]),
    # ── Step 6.6 -- forward-track every A/B/C pick from this run. record
    # captures entry prices; update fills any matured forward returns for
    # still-active positions across the whole pick history.
    ("outcome_tracker (record today's picks)", "pipeline/agents/outcome_tracker.py", ["backfill"]),
    ("outcome_tracker (update forward returns)", "pipeline/agents/outcome_tracker.py", ["update"]),
]


def main() -> None:
    print(f"{'='*72}\nFULL PIPELINE RE-RUN -- {len(STEPS)} steps\n{'='*72}", flush=True)
    results = []
    t0 = time.time()

    for i, (name, script, args) in enumerate(STEPS, start=1):
        print(f"\n{'#'*72}\n# STEP {i}/{len(STEPS)}: {name}\n{'#'*72}", flush=True)
        start = time.time()
        try:
            proc = subprocess.run([PY, script, *args], cwd=str(ROOT),
                                  timeout=STEP_TIMEOUT)
            status = "OK" if proc.returncode == 0 else f"EXIT {proc.returncode}"
        except subprocess.TimeoutExpired:
            status = f"TIMEOUT (>{STEP_TIMEOUT // 60}m)"
        except Exception as exc:
            status = f"ERROR {exc}"
        elapsed = (time.time() - start) / 60
        results.append((name, status, elapsed))
        print(f"\n# STEP {i} DONE -- {name}: {status} ({elapsed:.1f} min)", flush=True)

    total = (time.time() - t0) / 60
    print(f"\n{'='*72}\nFULL PIPELINE RE-RUN COMPLETE -- {total:.0f} min total\n{'='*72}")
    for name, status, elapsed in results:
        flag = "  " if status == "OK" else ">>"
        print(f"  {flag} {status:<16}{elapsed:>6.1f}m  {name}")
    ok = sum(1 for _, s, _ in results if s == "OK")
    print(f"\n  {ok}/{len(results)} steps completed cleanly.")


if __name__ == "__main__":
    main()
