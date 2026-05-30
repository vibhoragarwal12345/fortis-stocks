"""
Backfill earnings transcripts via SEC EDGAR for tickers in the universe.

Tries the last 2 fiscal quarters for each ticker, hitting the SEC route
defined in sec_transcript_fetcher. Caches all results in disk + the
earnings_transcripts table. Rate-limited automatically by the fetcher's
SecRateLimiter (~8 req/sec).

Usage:
    python pipeline/agents/backfill_transcripts.py            # default: holdings + recent A/B/C picks
    python pipeline/agents/backfill_transcripts.py --portfolio  # only portfolio holdings
    python pipeline/agents/backfill_transcripts.py --picks      # only recent picks
    python pipeline/agents/backfill_transcripts.py --tickers AAPL NVDA MSFT
        Single-shot backfill for a specific list.

At the end, prints a coverage breakdown by quality_grade.
"""

from __future__ import annotations

import logging
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402
from agents.sec_transcript_fetcher import fetch_full_transcript  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _db():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _recent_pick_tickers(db, lookback_days=90) -> set[str]:
    cutoff = (datetime.now(timezone.utc).date() -
              timedelta(days=lookback_days)).isoformat()
    rows = (db.table("ranked_focus_list").select("ticker")
            .in_("conviction_grade", ["A", "B", "C"])
            .gte("run_date", cutoff).execute().data or [])
    return {r["ticker"] for r in rows if r.get("ticker")}


def _recent_quarters(n: int = 2) -> list[tuple[int, int]]:
    """Last n quarters as (year, quarter) tuples, newest first."""
    today = datetime.now(timezone.utc).date()
    m, y = today.month, today.year
    cur_q = (m - 1) // 3 + 1
    out = []
    for _ in range(n):
        out.append((y, cur_q))
        cur_q -= 1
        if cur_q == 0:
            cur_q, y = 4, y - 1
    return out


def backfill(tickers: list[str], quarters: list[tuple[int, int]] | None = None):
    db = _db()
    tickers = sorted(set(tickers))
    quarters = quarters or _recent_quarters(2)
    log.info("backfill: %d tickers x %d quarters = %d fetches",
             len(tickers), len(quarters), len(tickers) * len(quarters))

    results = []
    for i, t in enumerate(tickers, start=1):
        for y, q in quarters:
            r = fetch_full_transcript(t, y, q, db=db)
            results.append({"ticker": t, "year": y, "quarter": q,
                            "quality": r["quality_grade"],
                            "path": r.get("source_path"),
                            "words": len(r["transcript_text"].split())})
        log.info("  progress: %d/%d tickers", i, len(tickers))

    # Coverage breakdown
    bar = "=" * 76
    print(f"\n{bar}\nBACKFILL COMPLETE -- {len(results)} fetches\n{bar}")
    print("\n[ COVERAGE BY QUALITY GRADE ]")
    for q, n in Counter(r["quality"] for r in results).most_common():
        pct = n / len(results) * 100
        print(f"  {q:<22}{n:>3}  ({pct:>5.1f}%)")

    print("\n[ COVERAGE BY SOURCE PATH ]")
    for p, n in Counter(r["path"] for r in results).most_common():
        print(f"  {(p or 'none'):<28}{n:>3}")

    # Companies that consistently came back press_release_only or worse
    by_t = {}
    for r in results:
        by_t.setdefault(r["ticker"], []).append(r)
    no_transcript_ever = sorted([
        t for t, rs in by_t.items()
        if all(r["quality"] in ("press_release_only", "unavailable") for r in rs)
    ])
    print(f"\n[ TICKERS THAT NEVER PRODUCED full_call/partial -- {len(no_transcript_ever)} ]")
    for t in no_transcript_ever[:40]:
        print(f"  {t}")
    if len(no_transcript_ever) > 40:
        print(f"  ... +{len(no_transcript_ever) - 40} more")

    # Word-count distribution for press_release_only (richer is better)
    pr_words = [r["words"] for r in results if r["quality"] == "press_release_only"]
    if pr_words:
        pr_words.sort()
        med = pr_words[len(pr_words) // 2]
        print(f"\n[ PRESS-RELEASE WORD COUNTS  (n={len(pr_words)}) ]")
        print(f"  min={min(pr_words)}  median={med}  max={max(pr_words)}")
    print(f"\n{bar}\n")


def main():
    args = sys.argv[1:]
    db = _db()
    if args and args[0] == "--tickers":
        tickers = [t.upper() for t in args[1:]]
    else:
        # Default: recent graded picks. Portfolio-driven backfill was
        # removed alongside the portfolio system.
        tickers = list(_recent_pick_tickers(db))
    if not tickers:
        log.warning("no tickers to backfill")
        sys.exit(0)
    backfill(tickers)


if __name__ == "__main__":
    main()
