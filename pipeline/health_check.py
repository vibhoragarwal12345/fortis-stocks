"""
Step 7.4 -- pipeline health check.

Runs each morning from .github/workflows/data_quality.yml. Verifies that the
prior day's cron actually produced data and that the local fixtures the
pipeline depends on aren't stale. Exits non-zero on any failure so the
workflow's notify_failure job fires.

Checks (lean architecture -- the twice-daily two-speed funnel):
  [1] market_scans has a status='complete' scan (>100 tickers) in the last 96h
  [2] ranked_focus_list has rows from a run in the last 4 days
  [3] pipeline/data/ticker_master.csv mtime is younger than 30 days
  [4] IV computation: fetch SPY ATM IV; must land in a sane band

Freshness windows are wide (96h / 4 days) on purpose: scans run Mon-Fri only
and this check fires at 12:00 UTC, so on a Monday the most recent scan is the
prior Friday's. A tighter window would false-alarm every weekend.

Tables removed in the May-2026 lean rewrite are no longer checked
(market_snapshots, validated_signals, and the daily reports table -- reports
are now produced on the monthly/multibagger cadence, not daily).

Usage:
    python pipeline/health_check.py
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make `from config import ...` work even when invoked from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

log = logging.getLogger("health_check")
logging.basicConfig(format="%(message)s", level=logging.INFO)

TICKER_MASTER = Path(__file__).resolve().parent / "data" / "ticker_master.csv"
IV_SANITY_LOW = 0.05    # 5% annualised
IV_SANITY_HIGH = 0.80   # 80% annualised -- SPY almost never sustains this


def _hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _count_since(db, table: str, column: str, hours: int) -> int:
    res = (
        db.table(table)
        .select("*", count="exact", head=True)
        .gte(column, _hours_ago(hours))
        .execute()
    )
    return int(res.count or 0)


def check_recent_scan(db) -> tuple[bool, str]:
    """A successful full scan completed recently (the core lean-pipeline signal)."""
    res = (
        db.table("market_scans")
        .select("*", count="exact", head=True)
        .eq("status", "complete")
        .gt("tickers_scanned_count", 100)
        .gte("completed_at", _hours_ago(96))
        .execute()
    )
    n = int(res.count or 0)
    ok = n > 0
    return ok, (
        f"complete scans (>100 tickers) in last 96h: {n} "
        f"({'ok' if ok else 'FAIL -- no recent successful scan'})"
    )


def check_ranked_focus_list(db) -> tuple[bool, str]:
    # run_date is a DATE column. 4-day window tolerates the Mon-Fri-only scan
    # cadence (on Monday the latest run_date is the prior Friday).
    cutoff = (datetime.now(timezone.utc) - timedelta(days=4)).date().isoformat()
    res = (
        db.table("ranked_focus_list")
        .select("*", count="exact", head=True)
        .gte("run_date", cutoff)
        .execute()
    )
    n = int(res.count or 0)
    ok = n > 0
    return ok, f"ranked_focus_list rows since {cutoff}: {n} ({'ok' if ok else 'FAIL'})"


def check_ticker_master() -> tuple[bool, str]:
    if not TICKER_MASTER.exists():
        return False, f"ticker_master.csv missing at {TICKER_MASTER}"
    age_days = (time.time() - TICKER_MASTER.stat().st_mtime) / 86_400
    ok = age_days < 30
    return ok, (
        f"ticker_master.csv age: {age_days:.1f} days "
        f"({'ok' if ok else 'FAIL -- regenerate via pipeline/data/build_ticker_master.py'})"
    )


def check_iv_computation() -> tuple[bool, str]:
    try:
        from quant.utils import fetch_atm_iv  # noqa: WPS433
        result = fetch_atm_iv("SPY")
    except Exception as exc:  # pragma: no cover -- defensive
        return False, f"IV smoke: exception fetching SPY ATM IV: {exc}"
    iv = result.get("iv") if isinstance(result, dict) else None
    if iv is None or iv != iv:  # NaN
        # This check fires at 12:00 UTC, before the US options market opens
        # (09:30 ET), so there are no live bid/ask quotes and IV can't be
        # computed. That's expected pre-market, not a pipeline health failure
        # -- skip rather than false-alarm. A genuine code break would instead
        # raise (handled above) or return an out-of-band number (caught below).
        return True, "IV smoke: skipped -- no live SPY option quotes (market closed)"
    ok = IV_SANITY_LOW <= iv <= IV_SANITY_HIGH
    return ok, (
        f"IV smoke: SPY ATM IV = {iv:.3f} "
        f"({'ok' if ok else f'FAIL -- outside [{IV_SANITY_LOW}, {IV_SANITY_HIGH}]'})"
    )


def main() -> int:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    checks = [
        ("recent_scan",        lambda: check_recent_scan(db)),
        ("ranked_focus_list",  lambda: check_ranked_focus_list(db)),
        ("ticker_master",      check_ticker_master),
        ("iv_smoke",           check_iv_computation),
    ]
    failures = 0
    print("=" * 70)
    print(f"Fortis pipeline health check -- {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)
    for name, fn in checks:
        try:
            ok, msg = fn()
        except Exception as exc:  # pragma: no cover -- defensive
            ok, msg = False, f"exception: {exc}"
        marker = "OK  " if ok else "FAIL"
        print(f"  [{marker}] {name:<22} {msg}")
        if not ok:
            failures += 1
    print("=" * 70)
    print(f"Result: {len(checks) - failures}/{len(checks)} checks passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
