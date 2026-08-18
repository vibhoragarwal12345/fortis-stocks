"""
News Resolver  (Part-1 data-integrity backfill)
Re-checks every news_items row's source-tagged ticker against the authoritative
ticker_master.csv. Two-stage verification per row:

  Stage 1 -- Company-name alias check: the headline must mention at least one
             alias of the source-tagged ticker (case-insensitive, word-
             bounded). The aliases come from SEC EDGAR / Finnhub via
             pipeline/data/build_ticker_master.py.
  Stage 2 -- Foreign-exchange filter: a headline saturated with
             India/Mumbai/BSE/NSE keywords cannot belong to a US-listed
             ticker (this is the VST Tillers -> NYSE:VST mis-tag).

A row passing Stage 1 with no Stage 2 mismatch keeps its assignment. Failing
rows have their sentiment_score nulled AND resolution_status set to
'unresolved' so future sentiment runs skip them.

Usage:
    python pipeline/agents/news_resolver.py
"""

import csv
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

MASTER_PATH = Path(__file__).resolve().parent.parent / "data" / "ticker_master.csv"
FOREIGN_KEYWORDS = re.compile(
    r"\b(bombay\s+stock\s+exchange|bse\s+(?:india|sensex)|nse\s+india|sensex|"
    r"nifty|rupee|crore|lakh|reserve\s+bank\s+of\s+india|sebi|mumbai|delhi|"
    r"kolkata|chennai|bengaluru|hyderabad|pune|ahmedabad)\b",
    re.IGNORECASE)

# Aliases this short or this generic would false-positive on too many headlines.
_ALIAS_BLOCKLIST = {"the", "and", "for", "co", "corp", "inc", "ltd", "plc",
                    "group", "trust", "holdings", "se", "ag", "sa", "nv"}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Master loader
# ══════════════════════════════════════════════════════════════════════════════

def _load_master() -> dict[str, dict]:
    if not MASTER_PATH.exists():
        raise FileNotFoundError(f"{MASTER_PATH} -- run build_ticker_master.py first")
    out: dict[str, dict] = {}
    with open(MASTER_PATH, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = [a.strip().lower() for a in (row.get("aliases") or "").split("|")]
            aliases = [a for a in raw if len(a) >= 3 and a not in _ALIAS_BLOCKLIST]
            out[row["ticker"]] = {
                "name": (row.get("company_name") or "").lower(),
                "aliases": aliases,
                "mic": row.get("mic"),
                "exchange_context": row.get("exchange_context"),
            }
    return out


def _stage1_alias_match(headline: str, master_row: dict) -> bool:
    """Does the headline mention at least one of this ticker's aliases?"""
    if not headline or not master_row:
        return False
    h = headline.lower()
    for alias in master_row["aliases"]:
        if re.search(r"\b" + re.escape(alias) + r"\b", h):
            return True
    return False


def _stage2_foreign_context(headline: str) -> bool:
    return bool(FOREIGN_KEYWORDS.search(headline or ""))


# ══════════════════════════════════════════════════════════════════════════════
# Backfill
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_all_news(db, page: int = 1000, only_unclassified: bool = True) -> list[dict]:
    """Page through news_items with keyset (cursor) pagination.

    NOT .range(): LIMIT/OFFSET forces Postgres to walk and discard every row
    before the offset, so a full pass is O(n^2). Once news_items passed ~500k
    rows this raised 57014 (statement timeout) at offset ~465000 and killed the
    resolver every day from 2026-07-15 onward. Because the resolver died, rows
    kept resolution_status = NULL, and sentiment_scorer -- which only accepts
    'verified' rows -- then had nothing to score or roll up. Keyset paging is
    constant-time per page and independent of table size.

    only_unclassified=True honours this module's own stated intent ("mark
    verified rows so a future run doesn't re-check them"), which the old
    unfiltered fetch never actually implemented -- every run re-read and
    re-wrote all ~600k rows. Pass False (CLI: --all) for a full re-scan, e.g.
    after the ticker master changes -- run_resync.py passes --all because its
    job is to clean the whole table, not just the new rows.
    """
    out, last = [], None
    while True:
        q = (db.table("news_items")
             .select("id,ticker,headline,sentiment_score,resolution_status")
             .order("id").limit(page))
        if only_unclassified:
            q = q.is_("resolution_status", "null")
        if last is not None:
            q = q.gt("id", last)
        chunk = q.execute().data or []
        out.extend(chunk)
        if len(chunk) < page:
            return out
        last = chunk[-1]["id"]


def _bulk_update(db, rows: list[dict], patch: dict, chunk: int = 500) -> int:
    """Apply `patch` to every row id, one request per `chunk` ids.

    The previous implementation issued one HTTP UPDATE per row through a
    ThreadPoolExecutor -- ~600k round-trips on a full pass. Each UPDATE also
    writes a new row version, so a large pass inflates the table (and the
    Supabase free-tier 500 MB budget) fast. Batching by id keeps the row-version
    count identical but collapses the request count by ~500x, and returns the
    number of rows actually written so the caller can report honestly.
    """
    done = 0
    for i in range(0, len(rows), chunk):
        ids = [r["id"] for r in rows[i:i + chunk]]
        try:
            db.table("news_items").update(patch).in_("id", ids).execute()
            done += len(ids)
        except Exception as exc:
            log.warning("bulk update failed for %d ids (%s): %s",
                        len(ids), ids[:3], exc)
    return done


def run(only_unclassified: bool = True) -> None:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    master = _load_master()
    log.info("ticker_master: %d verified US tickers loaded", len(master))

    rows = _fetch_all_news(db, only_unclassified=only_unclassified)
    log.info("news_items: %d rows to verify", len(rows))

    verified, unresolved_noalias, unresolved_foreign, not_in_master = [], [], [], []
    for r in rows:
        t = r.get("ticker")
        head = r.get("headline") or ""
        if t not in master:
            not_in_master.append(r)
            continue
        if _stage2_foreign_context(head):
            unresolved_foreign.append(r)
            continue
        if _stage1_alias_match(head, master[t]):
            verified.append(r)
        else:
            unresolved_noalias.append(r)

    log.info("Resolution: verified=%d  no_alias_match=%d  "
             "foreign_context=%d  ticker_not_in_master=%d",
             len(verified), len(unresolved_noalias),
             len(unresolved_foreign), len(not_in_master))

    # Mark failures. The "ticker_not_in_master" bucket also gets flagged --
    # a ticker we can't verify is one we shouldn't trust downstream.
    to_flag = unresolved_noalias + unresolved_foreign + not_in_master
    log.info("Flagging %d rows as resolution_status='unresolved' and nulling "
             "their sentiment_score", len(to_flag))

    nulled = 0
    if to_flag:
        nulled = _bulk_update(db, to_flag,
                              {"resolution_status": "unresolved",
                               "sentiment_score": None})
        log.info("Updated %d/%d rows", nulled, len(to_flag))

    # Mark verified rows so a future run doesn't re-check them.
    if verified:
        marked = _bulk_update(db, verified, {"resolution_status": "verified"})
        log.info("Marked %d/%d rows as 'verified'", marked, len(verified))

    # Final report
    bar = "=" * 70
    print(f"\n{bar}\nNEWS RESOLVER -- {len(rows)} rows scanned\n{bar}")
    print(f"  verified                 {len(verified):>6}")
    print(f"  unresolved (no alias)    {len(unresolved_noalias):>6}")
    print(f"  unresolved (foreign ctx) {len(unresolved_foreign):>6}")
    print(f"  ticker not in master     {len(not_in_master):>6}")
    pct = len(verified) / max(len(rows), 1) * 100
    print(f"\n  Pass rate: {pct:.1f}%  ({len(verified)}/{len(rows)})\n{bar}\n")


if __name__ == "__main__":
    # --all forces a full re-scan of every row (default: only unclassified).
    run(only_unclassified="--all" not in sys.argv)
