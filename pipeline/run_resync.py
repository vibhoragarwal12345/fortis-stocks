"""
Resync Orchestrator
After the news-resolver backfill and the bug fixes (critic Gemini-quota, BL
tilt sanity, dcf negative-equity guard), bring every downstream agent back
into sync by re-running them in strict dependency order. Each step is an
isolated subprocess so a single failure can't kill the chain.

Order (mirrors the data dependencies):
  1  news_resolver         clean the news_items table
  2  sentiment_scorer      rebuild rollups using only verified rows
  3  anomaly_detector      refresh flags
  4  run_factor_dcf        refresh DCF (picks up VST negative-equity guard)
  5  cross_validator       consume anomaly + clean sentiment
  6  ranking_engine        rebuild the focus list (all 3 run_types)
  7  catalyst_agent        refresh catalysts
  8  debate_synthesizer    regenerate top-30 theses
  9  critic_agent          critique with the Gemini-quota fix
  10 conviction_grader     assign grades incorporating critic verdicts
  11 run_black_litterman   re-optimize with the cap-aware tilt check

Usage:
    python pipeline/run_resync.py
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
STEP_TIMEOUT = 3600                                  # 60 min hard cap per step

STEPS = [
    ("news_resolver",                 "pipeline/agents/news_resolver.py", []),
    ("sentiment_scorer (rollup)",     "pipeline/processors/sentiment_scorer.py", ["midday"]),
    ("anomaly_detector",              "pipeline/agents/anomaly_detector.py", ["midday"]),
    ("run_factor_dcf (top 30)",       "pipeline/quant/run_factor_dcf.py", ["30"]),
    ("cross_validator",               "pipeline/agents/cross_validator.py", ["midday"]),
    ("ranking_engine (all 3 runs)",   "pipeline/agents/ranking_engine.py", []),
    ("catalyst_agent",                "pipeline/agents/catalyst_agent.py", []),
    ("debate_synthesizer (top 30)",   "pipeline/agents/debate_synthesizer.py", ["30"]),
    ("critic_agent (top 30)",         "pipeline/agents/critic_agent.py", ["30"]),
    ("conviction_grader",             "pipeline/agents/conviction_grader.py", ["midday"]),
    ("run_black_litterman",           "pipeline/quant/run_black_litterman.py", ["midday"]),
]


def main() -> None:
    print(f"{'='*72}\nRESYNC -- {len(STEPS)} steps\n{'='*72}", flush=True)
    results, t0 = [], time.time()

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
    print(f"\n{'='*72}\nRESYNC COMPLETE -- {total:.0f} min total\n{'='*72}")
    for name, status, elapsed in results:
        flag = "  " if status == "OK" else ">>"
        print(f"  {flag} {status:<14}{elapsed:>6.1f}m  {name}")
    ok = sum(1 for _, s, _ in results if s == "OK")
    print(f"\n  {ok}/{len(results)} steps clean.")


if __name__ == "__main__":
    main()
