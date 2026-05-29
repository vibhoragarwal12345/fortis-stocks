"""
Step 7.4 -- pipeline health check.

Runs each morning from .github/workflows/data_quality.yml. Verifies that the
prior day's cron actually produced data and that the local fixtures the
pipeline depends on aren't stale. Exits non-zero on any failure so the
workflow's notify_failure job fires.

Checks:
  [1] market_snapshots has rows from the last 36 hours
  [2] ranked_focus_list has rows from at least one run in the last 24 hours
  [3] validated_signals has rows from the last 24 hours
  [4] reports table has at least one row from the last 24 hours
  [5] pipeline/data/ticker_master.csv mtime is younger than 30 days
  [6] IV computation: fetch SPY ATM IV; must land in a sane band

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


def check_market_snapshots(db) -> tuple[bool, str]:
    n = _count_since(db, "market_snapshots", "snapshot_time", 36)
    ok = n > 100
    return ok, f"market_snapshots rows in last 36h: {n} ({'ok' if ok else 'FAIL -- expected > 100'})"


def check_ranked_focus_list(db) -> tuple[bool, str]:
    # run_date is a DATE column; compare against yesterday so we tolerate
    # the cron firing late or in a different timezone.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    res = (
        db.table("ranked_focus_list")
        .select("*", count="exact", head=True)
        .gte("run_date", cutoff)
        .execute()
    )
    n = int(res.count or 0)
    ok = n > 0
    return ok, f"ranked_focus_list rows since {cutoff}: {n} ({'ok' if ok else 'FAIL'})"


def check_validated_signals(db) -> tuple[bool, str]:
    n = _count_since(db, "validated_signals", "snapshot_time", 24)
    ok = n > 0
    return ok, f"validated_signals rows in last 24h: {n} ({'ok' if ok else 'FAIL'})"


def check_reports(db) -> tuple[bool, str]:
    n = _count_since(db, "reports", "generated_at", 24)
    ok = n > 0
    return ok, f"reports rows in last 24h: {n} ({'ok' if ok else 'FAIL'})"


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
    if iv is None or iv != iv:  # NaN check
        return False, f"IV smoke: SPY ATM IV unavailable ({result})"
    ok = IV_SANITY_LOW <= iv <= IV_SANITY_HIGH
    return ok, (
        f"IV smoke: SPY ATM IV = {iv:.3f} "
        f"({'ok' if ok else f'FAIL -- outside [{IV_SANITY_LOW}, {IV_SANITY_HIGH}]'})"
    )


def main() -> int:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    checks = [
        ("market_snapshots",   lambda: check_market_snapshots(db)),
        ("ranked_focus_list",  lambda: check_ranked_focus_list(db)),
        ("validated_signals",  lambda: check_validated_signals(db)),
        ("reports",            lambda: check_reports(db)),
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
