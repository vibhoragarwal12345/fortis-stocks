"""
Anomaly monitor -- "did the platform actually produce anything?"
================================================================

WHY THIS EXISTS
---------------
ticker_sentiment_rollup stopped updating on 2026-07-15 and nobody found out for
33 days. Every ingredient of that silence is worth naming, because this module
exists to remove each one:

  * data_harvest.yml marks every step `continue-on-error: true`, so a step that
    crashed outright still reported success. GitHub showed 33 consecutive green
    checkmarks.
  * The daily health check did not look at the rollup at all.
  * Its ticker_master staleness check COULD NOT FAIL in CI, because it measured
    file mtime and actions/checkout resets mtime on every run.
  * The failure notifier was gated on `if: env.WEBHOOK != ''` and
    SLACK_WEBHOOK_URL was never configured, so even a loud failure alerted
    nobody.

So: this checks OUTCOMES (did rows land in the table?) rather than exit codes,
every check is cheap and index-backed, every check is provably able to fail,
and the workflow that runs it opens a GitHub issue -- which emails the owner --
instead of a notifier that can silently no-op.

DESIGN RULES (learned the hard way)
-----------------------------------
1. Cheap. On 2026-08-19 a check I added used count="exact" over an unindexed
   column, took 4,916 ms, returned PostgREST 500 and failed the workflow while
   the system was perfectly healthy. Every query here is index-backed and
   bounded. A monitor that breaks production is worse than no monitor.
2. Tight windows. The first rollup check used a 4-day window, so when the
   2026-08-19 harvest produced no rollup it still reported "ok". A daily job
   gets a daily budget, not a four-day one.
3. Weekend-aware. Scans run Mon-Fri, so a Monday 09:00 check must tolerate
   Friday's data or it cries wolf every weekend -- and an alert that fires
   every weekend is an alert nobody reads.
4. Severity. CRITICAL = the product is wrong or absent and someone must act.
   WARNING = degraded, worth knowing, not worth waking up for. Only CRITICAL
   fails the process.

Usage:
    python -m pipeline.anomaly_monitor            # human-readable report
    python -m pipeline.anomaly_monitor --json     # machine-readable
Exit code: 0 = no critical anomaly, 1 = at least one CRITICAL.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

CRITICAL, WARNING, OK = "CRITICAL", "WARNING", "OK"

# Free-tier ceiling. Crossing it can put the project read-only, which is a
# platform outage, so it is deliberately alarmed well before the cliff.
# Thresholds leave runway to act rather than firing at the cliff, but are
# deliberately NOT tight: the database routinely peaks in the 470s after a day
# of update churn and drops again after the 03:30 compaction job (migration
# 060). Alarming at 470 would fire almost daily, and an alert that cries wolf
# is one that gets muted -- which is how 33 silent days happened.
# Revisit once a few post-compaction cycles have been observed.
DB_QUOTA_MB = 500
DB_WARN_MB = 455
DB_CRIT_MB = 485


@dataclass
class Check:
    name: str
    severity: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.severity == CRITICAL


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _business_hours_ago(hours: int) -> datetime:
    """`hours` ago, but skipping weekends.

    The scans run Mon-Fri. Measuring plain wall-clock staleness on a Monday
    morning flags Friday's perfectly good data, and a monitor that alerts every
    single weekend is one that gets muted -- which is how you end up back at 33
    silent days.
    """
    t = _now()
    remaining = hours
    while remaining > 0:
        t -= timedelta(hours=1)
        if t.weekday() < 5:          # Mon-Fri
            remaining -= 1
    return t


def _latest(db, table: str, column: str) -> datetime | None:
    """Newest value of a timestamp column. One indexed row, no COUNT."""
    rows = (db.table(table).select(column)
            .order(column, desc=True).limit(1).execute()).data or []
    if not rows or not rows[0].get(column):
        return None
    raw = str(rows[0][column])
    if len(raw) == 10:                                  # a DATE column
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def check_freshness(db, table: str, column: str, budget_h: int,
                    severity: str = CRITICAL, business_days: bool = True) -> Check:
    """`table` must have written a row within `budget_h` (business) hours."""
    try:
        latest = _latest(db, table, column)
    except Exception as exc:
        return Check(table, WARNING, f"could not read {table}.{column}: {exc}")
    if latest is None:
        return Check(table, severity, f"{table} is EMPTY -- nothing has ever been written")
    cutoff = _business_hours_ago(budget_h) if business_days else _now() - timedelta(hours=budget_h)
    age_h = (_now() - latest).total_seconds() / 3600
    if latest < cutoff:
        return Check(table, severity,
                     f"{table} last wrote {age_h:.0f}h ago (budget {budget_h}h) "
                     f"-- newest {latest:%Y-%m-%d %H:%M}Z")
    return Check(table, OK, f"{table} fresh ({age_h:.0f}h ago)")


def check_news_resolution(db, sample: int = 500) -> Check:
    """Fresh news is being classified -- the exact 2026-07 failure, caught early.

    Samples the newest rows by primary key rather than counting a window: an
    index scan (~7ms) instead of a sequential scan (~4,900ms).
    """
    rows = (db.table("news_items").select("id,resolution_status")
            .order("id", desc=True).limit(sample).execute()).data or []
    if not rows:
        return Check("news_resolution", CRITICAL, "news_items is empty")
    n_null = sum(1 for r in rows if r.get("resolution_status") is None)
    pct = n_null / len(rows) * 100
    if pct >= 90:
        return Check("news_resolution", CRITICAL,
                     f"{n_null}/{len(rows)} newest news rows unclassified ({pct:.0f}%) "
                     "-- news_resolver is not running; sentiment + rollup will go stale")
    if pct >= 50:
        return Check("news_resolution", WARNING,
                     f"{n_null}/{len(rows)} newest news rows unclassified ({pct:.0f}%)")
    return Check("news_resolution", OK, f"{n_null}/{len(rows)} unclassified ({pct:.0f}%)")


def check_scoring_backlog(db, sample: int = 1000) -> Check:
    """Verified news is actually being scored.

    Uses the partial index from migration 061; without it this exact shape took
    4,992 ms and timed out the harvest on 2026-08-19.
    """
    rows = (db.table("news_items").select("id")
            .is_("sentiment_score", "null").eq("resolution_status", "verified")
            .order("id").limit(sample).execute()).data or []
    n = len(rows)
    if n >= sample:
        return Check("scoring_backlog", WARNING,
                     f">={sample} verified news rows still unscored -- "
                     "sentiment_scorer is falling behind")
    return Check("scoring_backlog", OK, f"{n} rows awaiting scoring")


def check_db_size(db) -> Check:
    """Database size against the Supabase free-tier quota.

    On 2026-08-19 this hit 525 MB -- 25 MB OVER quota -- from a single day of
    update churn. Crossing the ceiling can flip the project read-only, so this
    alarms with room to act rather than at the cliff.
    """
    try:
        res = db.rpc("db_size_mb", {}).execute()
        mb = float(res.data)
    except Exception as exc:
        return Check("db_size", WARNING, f"could not read database size: {exc}")
    if mb >= DB_CRIT_MB:
        return Check("db_size", CRITICAL,
                     f"database {mb:.0f} MB of {DB_QUOTA_MB} MB quota "
                     f"({DB_QUOTA_MB - mb:.0f} MB left) -- read-only risk")
    if mb >= DB_WARN_MB:
        return Check("db_size", WARNING,
                     f"database {mb:.0f} MB of {DB_QUOTA_MB} MB quota "
                     f"({DB_QUOTA_MB - mb:.0f} MB left)")
    return Check("db_size", OK, f"database {mb:.0f} MB of {DB_QUOTA_MB} MB")


def check_llm_providers() -> Check:
    """At least one LLM provider actually answers.

    Groq retired llama-3.3-70b-versatile on ~2026-08-17 and every call 404'd for
    two days. Seven modules call Groq with no failover and silently served
    canned template text; nothing surfaced it because failover logs a WARNING.
    A live call is the only check that catches a retired model.
    """
    try:
        from llm import complete
        text, provider = complete("Reply with the single word: ok",
                                  system="Reply with exactly one word.",
                                  temperature=0.0, max_tokens=512)
    except Exception as exc:
        return Check("llm_providers", CRITICAL, f"LLM gateway raised: {exc}")
    if not text:
        return Check("llm_providers", CRITICAL,
                     "no LLM provider returned text -- every provider is down, "
                     "rate-limited, or pointed at a retired model")
    return Check("llm_providers", OK, f"{provider} responded")


def run_checks(db) -> list[Check]:
    return [
        # ── core product: if these are stale the dashboard is wrong ──────
        check_freshness(db, "market_scans",       "scan_timestamp", 20),
        check_freshness(db, "scan_results",       "created_at",     20),
        check_freshness(db, "ranked_focus_list",  "run_date",       30),
        # ── the 2026-07 outage, watched from three angles ───────────────
        check_freshness(db, "ticker_sentiment_rollup", "created_at", 30),
        check_news_resolution(db),
        check_scoring_backlog(db),
        # ── ingestion ───────────────────────────────────────────────────
        check_freshness(db, "news_items",      "fetched_at",    30),
        check_freshness(db, "options_signals", "snapshot_time", 30, WARNING),
        check_freshness(db, "ticker_debates",  "created_at",    30, WARNING),
        # ── downstream analytics (degraded, not broken) ─────────────────
        check_freshness(db, "validated_signals",      "created_at",  48, WARNING),
        check_freshness(db, "smart_money_intel",      "created_at",  48, WARNING),
        # Wall-clock, not business hours: these cadences are weekly/calendar,
        # and skipping weekends on a 10-day budget silently stretches it to ~14
        # calendar days -- which let a genuinely 12-day-stale fundamentals cache
        # report "fresh".
        check_freshness(db, "multibagger_candidates", "screen_date", 24 * 9,
                        WARNING, business_days=False),
        check_freshness(db, "fundamentals",           "fetched_at",  24 * 10,
                        WARNING, business_days=False),
        # ── platform health ─────────────────────────────────────────────
        check_db_size(db),
        check_llm_providers(),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--selftest", action="store_true",
                    help="inject a synthetic CRITICAL to prove the alert path "
                         "still reaches a human, without faking real data")
    args = ap.parse_args()

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    checks = run_checks(db)
    if args.selftest:
        # An alerting path nobody exercises is an alerting path nobody can
        # trust -- SLACK_WEBHOOK_URL sat unconfigured for months and silently
        # swallowed every alert. This injects a harmless synthetic CRITICAL so
        # the full chain (monitor -> issue -> email) can be proven end to end
        # on demand, without waiting for a real outage or faking data.
        checks.append(Check("selftest", CRITICAL,
                            "SYNTHETIC alert -- this is a drill, triggered by "
                            "--selftest. If you are reading this in your inbox, "
                            "anomaly alerting is working."))
    crit = [c for c in checks if c.severity == CRITICAL]
    warn = [c for c in checks if c.severity == WARNING]

    if args.json:
        print(json.dumps({
            "generated_at": _now().isoformat(),
            "critical": len(crit), "warning": len(warn),
            "checks": [asdict(c) for c in checks],
        }, indent=2))
    else:
        bar = "=" * 78
        print(f"{bar}\nCEFA anomaly monitor -- {_now():%Y-%m-%d %H:%M}Z\n{bar}")
        for c in checks:
            mark = {CRITICAL: "CRIT", WARNING: "WARN", OK: "OK  "}[c.severity]
            print(f"  [{mark}] {c.name:<26} {c.detail}")
        print(bar)
        print(f"{len(crit)} critical, {len(warn)} warning, "
              f"{len(checks) - len(crit) - len(warn)} ok")

    return 1 if crit else 0


if __name__ == "__main__":
    sys.exit(main())
