"""
Track-Record Scheduler
Thin wrapper that runs the outcome / backtest cadences described in Step 6.6:

  daily    -- outcome_tracker.record_recent  (capture entry prices for new picks)
              + outcome_tracker.update_outcomes  (forward-prices for active picks)
  weekly   -- outcome_tracker.compute_rollups  (Sunday night)
  monthly  -- backtest_engine.generate_performance_report  (1st of month)

RECORDING (why record_recent is first): the lean scan (run_scan.py) does NOT
record its own picks. The original recording step lived in run_full_pipeline.py,
which the lean rewrite replaced -- silently freezing the track record (picks
stopped being captured into pick_outcomes). The daily cadence now records the
last 14 days of graded runs first (idempotent upsert on
(ticker, recommended_date, recommended_run_type)) so no run is ever missed,
then fills forward returns for everything active.

Usage:
    python pipeline/run_track_record.py daily
    python pipeline/run_track_record.py weekly
    python pipeline/run_track_record.py monthly
    python pipeline/run_track_record.py all      # daily + weekly + monthly

Wire into cron / systemd timers / GitHub Actions as you prefer. Example crontab:

    # daily at 18:30 ET (after close)
    30 18 * * 1-5  cd /d/fortis-stocks && python pipeline/run_track_record.py daily
    # weekly Sunday 22:00
    0  22 * * 0    cd /d/fortis-stocks && python pipeline/run_track_record.py weekly
    # 1st of every month 02:00
    0   2 1 * *    cd /d/fortis-stocks && python pipeline/run_track_record.py monthly
"""

import logging
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

STEPS = {
    "daily":   [("outcome_tracker.record_recent",
                  "pipeline/agents/outcome_tracker.py", ["record-recent", "14"]),
                ("outcome_tracker.update_outcomes",
                  "pipeline/agents/outcome_tracker.py", ["update"])],
    "weekly":  [("outcome_tracker.compute_rollups",
                  "pipeline/agents/outcome_tracker.py", ["rollup"])],
    "monthly": [("backtest_engine.generate_performance_report",
                  "pipeline/agents/backtest_engine.py", ["report"])],
}


def _run(name, script, args):
    log.info("STEP: %s", name)
    t0 = time.time()
    try:
        proc = subprocess.run([PY, script, *args], cwd=str(ROOT), timeout=3600)
        status = "OK" if proc.returncode == 0 else f"EXIT {proc.returncode}"
    except subprocess.TimeoutExpired:
        status = "TIMEOUT"
    except Exception as exc:
        status = f"ERROR {exc}"
    log.info("STEP DONE: %s -- %s (%.1fs)", name, status, time.time() - t0)
    return status


def main():
    cadence = sys.argv[1].lower() if len(sys.argv) > 1 else "daily"
    if cadence == "all":
        order = ["daily", "weekly", "monthly"]
    elif cadence in STEPS:
        order = [cadence]
    else:
        log.error("usage: run_track_record.py [daily|weekly|monthly|all]")
        sys.exit(2)

    failed = 0
    for c in order:
        for name, script, args in STEPS[c]:
            status = _run(name, script, args)
            if status != "OK":
                failed += 1
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
