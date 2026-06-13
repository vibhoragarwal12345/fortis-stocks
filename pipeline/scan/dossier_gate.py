"""
Dossier Gate -- the dossier-completeness LABEL.
==================================================

The full picked shortlist (20-25 names) displays. This step flags which of
those picks carry a COMPLETED, FACT-CHECKED dossier vs. which are still being
assembled, so the UI can label the latter "dossier completing" and withhold
their unverified prose (rather than hiding the name and dropping below the
20-name floor). Runs after Layer 4 and, for the given scan:

  1. Marks ranked_focus_list.dossier_complete = true for rows that have the
     full dossier:
        catalyst_description      (catalyst_agent -- has deterministic
                                   template fallback, so it never gates a
                                   name out by itself)
        bull_case + bear_case     (debate_synthesizer, factchecked)
        critic_objection_level    (critic_agent, factchecked)
        conviction_grade          (conviction_grader)
        dossier_quality_grade     VERIFIED or PARTIALLY_VERIFIED
                                  (factcheck_agent over thesis + critique).
                                  NULL passes only for legacy rows written
                                  before migration 048 -- every post-048
                                  thesis carries a grade, so the legacy
                                  clause self-expires.
  2. Syncs scan_results.advanced to match: false for shortlisted names
     WITHOUT a complete dossier (hides them on every display surface --
     dashboard shortlist, ticker tape, scan history), true for names with
     one (so re-running agents to fill gaps then re-running the gate heals
     a previously demoted name). The focus list and landing page filter on
     dossier_complete directly.

If the LLM budget ran out mid-scan, the displayed list is simply shorter
that scan -- that is correct behavior, not a failure: the gate always exits
0 and reports coverage in the logs + market_scans.notes (via run_scan).

CLI
  python -m pipeline.scan.dossier_gate --scan-id 123
"""

from __future__ import annotations

import argparse
import logging
import sys
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

# Every dossier section that must be present before a name may display.
_REQUIRED_FIELDS = (
    "catalyst_description",
    "bull_case",
    "bear_case",
    "critic_objection_level",
    "conviction_grade",
)
_PASSING_GRADES = ("VERIFIED", "PARTIALLY_VERIFIED")


def _is_complete(row: dict) -> bool:
    if any(not row.get(f) for f in _REQUIRED_FIELDS):
        return False
    grade = row.get("dossier_quality_grade")
    # NULL = legacy pre-048 dossier (grandfathered by the migration); every
    # thesis written since carries a factcheck grade, so this cannot admit
    # a new unverified dossier.
    return grade is None or grade in _PASSING_GRADES


def gate(scan_id: int) -> tuple[int, int]:
    """Apply the invariant. Returns (complete, total) for the scan."""
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    rows = (db.table("ranked_focus_list")
            .select("ticker,rank," + ",".join(_REQUIRED_FIELDS)
                    + ",dossier_quality_grade")
            .eq("scan_id", scan_id)
            .order("rank").execute().data or [])
    if not rows:
        log.warning("dossier gate: no ranked_focus_list rows for scan_id=%d",
                    scan_id)
        return 0, 0

    complete = [r["ticker"] for r in rows if _is_complete(r)]
    bare = [r["ticker"] for r in rows if not _is_complete(r)]
    now = datetime.now(timezone.utc).isoformat()

    # The whole picked shortlist (20-25 names) DISPLAYS — that's the product
    # rule. `dossier_complete` is now a QUALITY LABEL, not a hide-filter: names
    # with the full fact-checked dossier are marked complete; the rest display
    # too but the UI labels them "dossier completing" and withholds their
    # unverified prose. So we no longer touch scan_results.advanced.
    if complete:
        (db.table("ranked_focus_list")
           .update({"dossier_complete": True, "dossier_completed_at": now})
           .eq("scan_id", scan_id).in_("ticker", complete).execute())
    if bare:
        (db.table("ranked_focus_list")
           .update({"dossier_complete": False})
           .eq("scan_id", scan_id).in_("ticker", bare).execute())

    log.info("dossier gate scan_id=%d: %d/%d picks carry a complete dossier",
             scan_id, len(complete), len(rows))
    if bare:
        log.info("dossier gate: %d name(s) shown as 'dossier completing': %s",
                 len(bare), ", ".join(bare))
    return len(complete), len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-id", type=int, required=True)
    args = ap.parse_args()
    gate(args.scan_id)
    return 0  # a shorter displayed list is never a pipeline failure


if __name__ == "__main__":
    sys.exit(main())
