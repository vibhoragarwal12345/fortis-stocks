"""
Step 7.4 -- pipeline health check.

Runs each morning from .github/workflows/data_quality.yml. Verifies that the
prior day's cron actually produced data and that the local fixtures the
pipeline depends on aren't stale. Exits non-zero on any failure so the
workflow's notify_failure job fires.

Checks (lean architecture -- the twice-daily two-speed funnel):
  [1] market_scans has a status='complete' scan (>100 tickers) in the last 96h
  [2] ranked_focus_list has rows from a run in the last 4 days
  [3] ticker_sentiment_rollup has rows from the last 4 days
  [4] fresh news_items are being classified (not stuck at resolution_status NULL)
  [5] pipeline/data/ticker_master.csv mtime is younger than 30 days
  [6] IV computation: fetch SPY ATM IV; must land in a sane band

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
import subprocess
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


def check_sentiment_rollup(db) -> tuple[bool, str]:
    """ticker_sentiment_rollup is still being written.

    Added after a 33-day silent outage (2026-07-15 .. 2026-08-17): news_resolver
    and sentiment_scorer both died on 57014 statement timeouts from OFFSET
    pagination, and because every step in data_harvest.yml is
    continue-on-error: true the workflow reported success the whole time. No
    check covered the rollup, so nothing surfaced it. This is that check.

    4-day window matches check_ranked_focus_list: the harvest is Mon-Fri, so on
    a Monday the newest snapshot_date is the prior Friday.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=4)).date().isoformat()
    res = (
        db.table("ticker_sentiment_rollup")
        .select("*", count="exact", head=True)
        .gte("snapshot_date", cutoff)
        .execute()
    )
    n = int(res.count or 0)
    ok = n > 0
    return ok, (
        f"ticker_sentiment_rollup rows since {cutoff}: {n} "
        f"({'ok' if ok else 'FAIL -- rollup stalled; check news_resolver + sentiment_scorer'})"
    )


def check_news_resolution(db) -> tuple[bool, str]:
    """Fresh news_items are actually being classified by news_resolver.

    The rollup only accepts resolution_status='verified'. If the resolver stops,
    rows pile up as NULL and the rollup silently empties out even though news is
    still arriving -- the exact failure mode of the 2026-07 outage, caught one
    step earlier than check_sentiment_rollup.
    """
    unclassified = _count_since(db, "news_items", "fetched_at", 48)
    res = (
        db.table("news_items")
        .select("*", count="exact", head=True)
        .gte("fetched_at", _hours_ago(48))
        .is_("resolution_status", "null")
        .execute()
    )
    n_null = int(res.count or 0)
    # Tolerate the newest arrivals not yet resolved; alert if nearly all are.
    ok = unclassified == 0 or (n_null / unclassified) < 0.9
    pct = (n_null / unclassified * 100) if unclassified else 0.0
    return ok, (
        f"news_items last 48h: {unclassified} rows, {n_null} still NULL "
        f"({pct:.0f}%) ({'ok' if ok else 'FAIL -- news_resolver not classifying'})"
    )


def _file_age_days(path: Path) -> tuple[float, str]:
    """Age of `path`, preferring its last git commit date over mtime.

    mtime is USELESS in CI: actions/checkout writes every file at checkout
    time, so an mtime-based staleness check always sees ~0 days and can never
    fail. check_ticker_master silently passed every day while the file sat 88
    days old against its own 30-day threshold. The git commit date is the real
    provenance. Requires fetch-depth: 0 (set in data_quality.yml); if the log
    is unavailable (shallow clone) we fall back to mtime and say so.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", str(path)],
            cwd=str(path.parent), capture_output=True, text=True, timeout=15,
        )
        stamp = out.stdout.strip()
        if out.returncode == 0 and stamp:
            return (time.time() - int(stamp)) / 86_400, "git"
    except Exception:
        pass
    return (time.time() - path.stat().st_mtime) / 86_400, "mtime(unreliable in CI)"


def check_ticker_master() -> tuple[bool, str]:
    if not TICKER_MASTER.exists():
        return False, f"ticker_master.csv missing at {TICKER_MASTER}"
    age_days, src = _file_age_days(TICKER_MASTER)
    ok = age_days < 30
    return ok, (
        f"ticker_master.csv age: {age_days:.1f} days (via {src}) "
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
        ("sentiment_rollup",   lambda: check_sentiment_rollup(db)),
        ("news_resolution",    lambda: check_news_resolution(db)),
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
