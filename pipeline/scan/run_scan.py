"""
Two-speed funnel orchestrator.
==============================

Single entry point for an end-to-end market scan. Sequenced as:

    Layer 1   layer1_fast_scan -- ~3-5k tickers, pure math, ~3-5 min
    Layer 2   layer2_rank      -- score + shortlist top N -> ranked_focus_list
    Bands     price_bands      -- zero-drift Monte Carlo price cone per pick
                                  (deterministic; runs even if the LLM budget
                                  is spent, so every name has honest levels)
    Layer 3   deep agents      -- catalyst, smart_money, debate, critic on top N
    Layer 4   conviction grade -- conviction_grader on the top N
    Gate      dossier_gate     -- only names with a complete, fact-checked
                                  dossier (bull+bear+thesis+price band) stay
                                  on the displayed shortlist

The shortlist N is sized to what the free-LLM budget reliably covers per
scan (DEFAULT_TOP_N = 20): 4 LLM calls per dossier x 3 scans/day across
Groq + Gemini (+ Cerebras/NVIDIA/OpenRouter overflow) -- measured June 2026,
when 30-name shortlists shipped only 19-27 dossiers on strained days. Every
displayed name must carry a full dossier; a shorter list when budget runs
out is correct behavior.

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
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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

# Whole-scan soft deadline. The CI workflow hard-kills the job at its
# timeout-minutes and a hard kill loses the dossier gate + finalize (both
# scans on June 12 2026 died mid-critic: market_scans stuck 'running',
# scan_state stuck, dashboard stale). The orchestrator therefore stops
# STARTING enrichment steps once this budget is spent and goes straight to
# gate + finalize -- a degraded-but-finalized scan instead of a dead one.
# Keep comfortably below the workflow timeout (55 min).
SOFT_DEADLINE_SEC = int(os.environ.get("SCAN_SOFT_DEADLINE_SEC", 46 * 60))

# Per-scan dossier pool. Layer 3 generates dossiers CONCURRENTLY
# (DOSSIER_CONCURRENCY, default 5), and the dossier gate trims any name whose
# thesis/critique/bear fails factcheck (~30-35% on the strict bar). We pick 35
# so ~20-25 FULLY-VERIFIED dossiers survive to display. Budget is not the
# constraint (Cerebras ~1M tok/day) -- concurrency + the soft deadline are.
# 2 daily scans now (post-close dropped June 14 2026).
DEFAULT_TOP_N = 35


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


def _run_step(name: str, cmd: list[str],
              timeout_sec: int = STEP_TIMEOUT_SEC) -> tuple[bool, str]:
    """Run an external Python step. Returns (ok, summary)."""
    log.info("→ %s", name)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), timeout=timeout_sec)
        ok = proc.returncode == 0
        elapsed = time.time() - t0
        msg = f"{name}: rc={proc.returncode} in {elapsed:.0f}s"
        if ok:
            log.info("  ✓ %s", msg)
        else:
            log.warning("  ✗ %s", msg)
        return ok, msg
    except subprocess.TimeoutExpired:
        msg = f"{name}: TIMEOUT after {timeout_sec}s"
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
    top_n: int = DEFAULT_TOP_N,
    sample: int | None = None,
    skip_layer3: bool = False,
    skip_layer4: bool = False,
) -> int:
    db = _db()
    t_total = time.time()

    # Reap orphaned scans: a prior run hard-killed mid-flight (CI timeout,
    # crash) leaves market_scans stuck at status='running' forever -- it
    # clutters scan history and shows as a permanent "running" row. Mark any
    # 'running' row older than 2h as 'failed' before opening this one.
    try:
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        db.table("market_scans").update({
            "status": "failed",
            "notes": "auto-failed: orphaned 'running' scan reaped by a later run",
        }).eq("status", "running").lt("started_at", stale_cutoff).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("stale-scan reaper failed: %s", exc)

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
    complete: int | None = None   # # of complete dossiers; set by the gate below

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

    # ── Price reference bands (deterministic; always runs) ──────────────
    # Zero-drift Monte Carlo cone per pick from price history -- no LLM, so
    # every shortlisted name gets honest reference levels even when the
    # Layer-3 budget is spent. The dossier gate requires price_reference.
    if shortlist > 0:
        ok, msg = _run_step(
            "Price reference bands",
            [PY, "-m", "pipeline.scan.price_bands", "--scan-id", str(scan_id)],
        )
        if not ok:
            soft_failures += 1
            notes.append(f"[non-fatal] {msg}")

    # Enrichment steps run against the soft deadline: once the budget is
    # spent we stop STARTING steps and fall through to gate + finalize.
    # Each started step is also capped to the time actually left.
    def _remaining_sec() -> int:
        return int(SOFT_DEADLINE_SEC - (time.time() - t_total))

    def _deadline_reached(name: str) -> bool:
        if _remaining_sec() >= 120:
            return False
        nonlocal soft_failures
        soft_failures += 1
        notes.append(f"[non-fatal] {name}: skipped -- soft deadline "
                     f"({SOFT_DEADLINE_SEC // 60}m) reached; finalizing scan")
        log.warning("  ⏱ %s skipped -- soft deadline reached", name)
        return True

    # ── Layer 3 (expensive, can be skipped on quick refreshes) ──────────
    if not skip_layer3 and shortlist > 0:
        # Agents that write dossier sections get --scan-id for precise
        # lineage (3 scans/day share run_date + run_type, so date-based
        # selection mixes scans). smart_money_intel is keyed per ticker,
        # not per scan, and keeps its positional (run_type, limit) form.
        for name, cmd in [
            ("catalyst_agent",
             [PY, "pipeline/agents/catalyst_agent.py",
              "--scan-id", str(scan_id)]),
            ("smart_money_intel",
             [PY, "pipeline/agents/smart_money_intel.py", "midday", str(top_n)]),
            ("debate_synthesizer",
             [PY, "pipeline/agents/debate_synthesizer.py", str(top_n),
              "--scan-id", str(scan_id)]),
            ("critic_agent",
             [PY, "pipeline/agents/critic_agent.py", str(top_n),
              "--scan-id", str(scan_id)]),
        ]:
            if _deadline_reached(f"Layer 3: {name}"):
                continue
            ok, msg = _run_step(f"Layer 3: {name}", cmd,
                                timeout_sec=min(STEP_TIMEOUT_SEC,
                                                max(120, _remaining_sec())))
            if not ok:
                soft_failures += 1
                notes.append(f"[non-fatal] {msg}")
        db.table("market_scans").update({
            "layer3_completed_at": _now(),
        }).eq("id", scan_id).execute()

    # ── Layer 4 ─────────────────────────────────────────────────────────
    if not skip_layer4 and shortlist > 0 and not _deadline_reached("Layer 4 conviction_grader"):
        ok, msg = _run_step(
            "Layer 4 conviction_grader",
            [PY, "pipeline/agents/conviction_grader.py", "scan"],
            timeout_sec=min(STEP_TIMEOUT_SEC, max(120, _remaining_sec())),
        )
        if not ok:
            soft_failures += 1
            notes.append(f"[non-fatal] {msg}")

    # ── Dossier gate: hide picks that lack a complete fact-checked dossier ──
    # A name DISPLAYS only with a complete, fact-checked dossier (owner directive
    # June 2026: bare/half-built dossiers "look unprofessional"). The gate sets
    # dossier_complete and syncs scan_results.advanced=false to hide the rest on
    # every surface. So the displayed count == `complete` below -- which is why a
    # truncated run can look empty, and why the promotion guard further down keeps
    # a thin run off the dashboard. A gate-step crash is non-fatal (recorded).
    if not skip_layer3 and shortlist > 0:
        ok, msg = _run_step(
            "Dossier gate",
            [PY, "-m", "pipeline.scan.dossier_gate", "--scan-id", str(scan_id)],
        )
        if not ok:
            soft_failures += 1
            notes.append(f"[non-fatal] {msg}")
        # Coverage is informational now (not a hide-count); record it.
        cnt = (
            db.table("ranked_focus_list")
              .select("ticker", count="exact")
              .eq("scan_id", scan_id)
              .eq("dossier_complete", True)
              .limit(1)
              .execute()
        )
        complete = int(getattr(cnt, "count", 0) or 0)
        if complete < shortlist:
            notes.append(f"dossier coverage {complete}/{shortlist}: "
                         f"{shortlist - complete} pick(s) shown as 'dossier "
                         "completing' (full fact-check still assembling)")
        log.info("Dossier gate: %d/%d picks carry a complete dossier", complete, shortlist)

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

    # Promotion guard: a fresh scan only becomes the DISPLAYED scan if it carries
    # enough complete, fact-checked dossiers to look credible to a client. A run
    # that got truncated (soft-deadline hit, agents skipped, or -- on a laptop --
    # the machine slept mid-scan) can finish 'complete' on the Layer-1/2 core yet
    # produce only a handful of dossiers. Promoting it would EMPTY the dashboard.
    # Instead we keep the last good scan displayed and record why. (June 15 2026:
    # a sleep-truncated local run with debate skipped became 'latest' and the
    # client-facing dashboard looked broken; this is the guard against that.)
    # CI (Linux) never sleeps, so this almost never trips there -- it's a floor.
    min_display = int(os.getenv("SCAN_MIN_DISPLAY_DOSSIERS", "12"))
    prior_id = None
    try:
        prior = db.table("scan_state").select("latest_scan_id").eq("id", 1).execute().data or []
        prior_id = (prior[0] if prior else {}).get("latest_scan_id")
    except Exception:  # noqa: BLE001
        pass

    promote = status == "complete" and not (
        complete is not None and complete < min_display and prior_id and prior_id != scan_id
    )

    # Update the shared scan_state so the dashboard's refresh control flips
    # back to idle and points at this scan (or surfaces the failure).
    if status == "complete" and promote:
        _set_scan_state(
            db,
            current_status="idle",
            latest_scan_id=scan_id,
            latest_scan_completed_at=_now(),
            running_since=None,
            last_error=None,
        )
    elif status == "complete":
        # Completed but too thin to display -- keep the prior good scan up.
        msg = (f"scan {scan_id} NOT promoted to dashboard: only {complete} "
               f"complete dossier(s) (< {min_display} floor); kept scan "
               f"{prior_id} displayed")
        notes.append(msg)
        log.warning(msg)
        db.table("market_scans").update({"notes": "; ".join(notes)}).eq("id", scan_id).execute()
        _set_scan_state(db, current_status="idle", running_since=None, last_error=None)
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
    ap.add_argument("--top-n",       type=int, default=DEFAULT_TOP_N)
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
