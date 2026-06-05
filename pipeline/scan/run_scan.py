"""
Two-speed funnel orchestrator.
==============================

Single entry point for an end-to-end market scan. Sequenced as:

    Layer 1   layer1_fast_scan -- ~3-5k tickers, pure math, ~3-5 min
    Layer 2   layer2_rank      -- score + shortlist top N -> ranked_focus_list
    Layer 3   deep agents      -- catalyst, smart_money, debate, critic on top N
    Layer 4   conviction grade -- conviction_grader on the top N

Persists scan progress into the `market_scans` row created at start. If any
layer fails the row gets status='failed' with a notes column explaining what
broke; the next scheduled run starts fresh.

The expensive Layer 3 agents are called as subprocesses so a single agent
crash can't take the whole orchestrator down. Output streams to stdout/
stderr; we log status + duration per step.

CLI
  python -m pipeline.scan.run_scan --scan-type intraday
  python -m pipeline.scan.run_scan --scan-type intraday --sample 500
  python -m pipeline.scan.run_scan --scan-type premarket --top-n 50 --skip-layer3
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
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

ROOT = Path(__file__).resolve().parent.parent.parent
PY = sys.executable
STEP_TIMEOUT_SEC = 1800  # 30 min per step ceiling


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _set_scan_state(db, **fields) -> None:
    """Upsert the singleton scan_state row (id=1) that the dashboard's manual
    refresh + status polling read. Best-effort: a state-write failure must
    never crash a scan."""
    try:
        fields["id"] = 1
        fields["updated_at"] = _now()
        db.table("scan_state").upsert(fields, on_conflict="id").execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("scan_state update failed (%s): %s", fields, exc)


def _run_step(name: str, cmd: list[str]) -> tuple[bool, str]:
    """Run an external Python step. Returns (ok, summary)."""
    log.info("→ %s", name)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), timeout=STEP_TIMEOUT_SEC)
        ok = proc.returncode == 0
        elapsed = time.time() - t0
        msg = f"{name}: rc={proc.returncode} in {elapsed:.0f}s"
        if ok:
            log.info("  ✓ %s", msg)
        else:
            log.warning("  ✗ %s", msg)
        return ok, msg
    except subprocess.TimeoutExpired:
        msg = f"{name}: TIMEOUT after {STEP_TIMEOUT_SEC}s"
        log.warning("  ✗ %s", msg)
        return False, msg
    except Exception as exc:
        msg = f"{name}: ERROR {exc}"
        log.warning("  ✗ %s", msg)
        return False, msg


# ──────────────────────────────────────────────────────────────────────────
#  Main orchestration
# ──────────────────────────────────────────────────────────────────────────

def run(
    scan_type: str,
    top_n: int = 80,
    sample: int | None = None,
    skip_layer3: bool = False,
    skip_layer4: bool = False,
) -> int:
    db = _db()
    t_total = time.time()

    # Mark the shared scan_state 'running' so the dashboard reflects it
    # immediately (covers cron runs; the manual trigger endpoint also sets
    # this before dispatch).
    _set_scan_state(
        db,
        current_status="running",
        running_since=_now(),
        triggered_by="manual" if scan_type == "manual" else "cron",
        last_error=None,
    )

    # 1. Open the market_scans row.
    scan_row = (
        db.table("market_scans")
          .insert({
              "scan_type": scan_type,
              "status":    "running",
              "started_at": _now(),
          })
          .execute()
          .data[0]
    )
    scan_id = int(scan_row["id"])
    log.info("Opened market_scans id=%d (%s)", scan_id, scan_type)

    notes: list[str] = []
    # Layers 1-2 are the pure-math core: without them the scan has no data,
    # so their failure is fatal (status='failed'). Layers 3-4 are LLM-driven
    # enrichments (catalyst/smart-money/debate/critic/grade); a transient LLM
    # outage or rate-limit there must NOT blank the dashboard, so those
    # failures are recorded in `notes` but the scan still completes.
    critical_failures = 0
    soft_failures = 0

    # ── Layer 1 ─────────────────────────────────────────────────────────
    t0 = time.time()
    args = ["-m", "pipeline.scan.layer1_fast_scan", "--scan-id", str(scan_id)]
    if sample:
        args += ["--sample", str(sample)]
    ok, msg = _run_step("Layer 1 fast scan", [PY, *args])
    if not ok:
        critical_failures += 1
        notes.append(msg)
    layer1_seconds = int(time.time() - t0)

    # Count layer-1 rows for the metadata + dashboard.
    cnt = (
        db.table("scan_results")
          .select("ticker", count="exact")
          .eq("scan_id", scan_id)
          .limit(1)
          .execute()
    )
    tickers_scanned = int(getattr(cnt, "count", 0) or 0)
    db.table("market_scans").update({
        "tickers_scanned_count": tickers_scanned,
        "layer1_completed_at":    _now(),
    }).eq("id", scan_id).execute()
    log.info("Layer 1 done: %d tickers scanned in %ds", tickers_scanned, layer1_seconds)

    # ── Layer 2 ─────────────────────────────────────────────────────────
    args = [
        "-m", "pipeline.scan.layer2_rank",
        "--scan-id", str(scan_id),
        "--top-n",   str(top_n),
    ]
    ok, msg = _run_step("Layer 2 rank", [PY, *args])
    if not ok:
        critical_failures += 1
        notes.append(msg)

    cnt = (
        db.table("scan_results")
          .select("ticker", count="exact")
          .eq("scan_id", scan_id)
          .eq("advanced", True)
          .limit(1)
          .execute()
    )
    shortlist = int(getattr(cnt, "count", 0) or 0)
    db.table("market_scans").update({
        "shortlist_count":     shortlist,
        "layer2_completed_at": _now(),
    }).eq("id", scan_id).execute()
    log.info("Layer 2 done: shortlist=%d", shortlist)

    # ── Layer 3 (expensive, can be skipped on quick refreshes) ──────────
    if not skip_layer3 and shortlist > 0:
        # Each subprocess hits ranked_focus_list filtered by run_type=
        # 'midday' (the existing module-level constant in debate +
        # critic). Layer 2 wrote the shortlist under that same run_type,
        # so the agents see exactly the names we want them to analyse.
        for name, cmd in [
            ("catalyst_agent",
             [PY, "pipeline/agents/catalyst_agent.py"]),
            ("smart_money_intel",
             [PY, "pipeline/agents/smart_money_intel.py", "midday", str(top_n)]),
            ("debate_synthesizer",
             [PY, "pipeline/agents/debate_synthesizer.py", str(top_n)]),
            ("critic_agent",
             [PY, "pipeline/agents/critic_agent.py", str(top_n)]),
        ]:
            ok, msg = _run_step(f"Layer 3: {name}", cmd)
            if not ok:
                soft_failures += 1
                notes.append(f"[non-fatal] {msg}")
        db.table("market_scans").update({
            "layer3_completed_at": _now(),
        }).eq("id", scan_id).execute()

    # ── Layer 4 ─────────────────────────────────────────────────────────
    if not skip_layer4 and shortlist > 0:
        ok, msg = _run_step(
            "Layer 4 conviction_grader",
            [PY, "pipeline/agents/conviction_grader.py", "scan"],
        )
        if not ok:
            soft_failures += 1
            notes.append(f"[non-fatal] {msg}")

    # Count final graded rows.
    cnt = (
        db.table("ranked_focus_list")
          .select("ticker", count="exact")
          .eq("scan_id", scan_id)
          .not_.is_("conviction_grade", "null")
          .limit(1)
          .execute()
    )
    graded = int(getattr(cnt, "count", 0) or 0)

    # The scan is 'complete' as long as the pure-math core (Layers 1-2)
    # succeeded. LLM-enrichment failures (Layers 3-4) are surfaced in notes
    # and the logs but do not invalidate the scan or fail the CI job --
    # otherwise a single rate-limited agent would hide a full, fresh scan
    # from the dashboard.
    status = "complete" if critical_failures == 0 else "failed"
    if soft_failures:
        log.warning(
            "%d Layer-3/4 enrichment step(s) degraded (e.g. LLM rate-limit); "
            "scan still marked '%s'. See notes.", soft_failures, status,
        )
    db.table("market_scans").update({
        "status":               status,
        "graded_count":         graded,
        "layer4_completed_at":  _now(),
        "completed_at":         _now(),
        "notes":                "; ".join(notes) if notes else None,
    }).eq("id", scan_id).execute()

    # Update the shared scan_state so the dashboard's refresh control flips
    # back to idle and points at this scan (or surfaces the failure).
    if status == "complete":
        _set_scan_state(
            db,
            current_status="idle",
            latest_scan_id=scan_id,
            latest_scan_completed_at=_now(),
            running_since=None,
            last_error=None,
        )
    else:
        _set_scan_state(
            db,
            current_status="failed",
            running_since=None,
            last_error=("; ".join(notes)[:500] if notes else "scan failed"),
        )

    total = time.time() - t_total
    log.info(
        "Scan id=%d %s: scanned=%d shortlist=%d graded=%d in %.0fs "
        "(%d critical, %d soft failures)",
        scan_id, status, tickers_scanned, shortlist, graded, total,
        critical_failures, soft_failures,
    )
    return 0 if critical_failures == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--scan-type",
        choices=["premarket", "intraday", "postclose", "manual"],
        default="manual",
    )
    ap.add_argument("--top-n",       type=int, default=80)
    ap.add_argument("--sample",      type=int, default=None,
                    help="Layer-1 ticker cap (dev mode only).")
    ap.add_argument("--skip-layer3", action="store_true",
                    help="Skip the deep-research agents (LLM-heavy).")
    ap.add_argument("--skip-layer4", action="store_true",
                    help="Skip conviction grading.")
    args = ap.parse_args()
    return run(
        scan_type=args.scan_type,
        top_n=args.top_n,
        sample=args.sample,
        skip_layer3=args.skip_layer3,
        skip_layer4=args.skip_layer4,
    )


if __name__ == "__main__":
    sys.exit(main())
