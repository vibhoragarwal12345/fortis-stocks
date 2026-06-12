"""Scan-target resolution for Layer-3/4 agents.

Post-lean architecture runs three scans per weekday and they all share
run_date + run_type='midday', so date-based row selection mixes scans:
agents would analyze an interleaved union of the day's shortlists and the
latest scan's tail ranks never got dossiers. scan_id is the real lineage
key -- run_scan passes `--scan-id N` to every agent; date-based selection
remains only as a fallback for standalone runs.
"""

from __future__ import annotations

import sys


def argv_scan_id(argv: list[str] | None = None) -> int | None:
    """Extract `--scan-id N` from argv (None when absent/malformed)."""
    args = sys.argv if argv is None else argv
    if "--scan-id" not in args:
        return None
    i = args.index("--scan-id")
    try:
        return int(args[i + 1])
    except (IndexError, ValueError):
        return None


def strip_scan_id(args: list[str]) -> list[str]:
    """Remove `--scan-id N` so positional parsing stays untouched."""
    out: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--scan-id":
            skip = True
            continue
        out.append(a)
    return out
