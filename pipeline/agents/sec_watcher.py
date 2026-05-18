"""
SEC EDGAR Watcher Agent
Polls SEC EDGAR's "recent filings" ATOM feeds for 8-K, Form 4 (insider
transactions), and -- during 13F season -- 13F-HR (institutional holdings).
Resolves each filer's CIK to a ticker and stores filings in sec_filings.

Note on ticker resolution:
  The companyfacts XBRL API (data.sec.gov/api/xbrl/companyfacts) returns
  financial facts only -- it does NOT contain ticker symbols. The canonical
  CIK->ticker map is SEC's company_tickers.json, downloaded once here. That
  is also one request instead of one-per-filing, which is friendlier to the
  rate limit. Form 4 feeds list the reporting *insider's* CIK (no ticker),
  so those rows are stored with ticker = NULL, as expected.

SEC compliance:
  - Declares a User-Agent identifying the application and a contact email.
  - Rate-limited safely under SEC's 10 requests/second ceiling.

Usage:
    python pipeline/agents/sec_watcher.py
"""

import calendar
import logging
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import feedparser
import requests
from supabase import create_client

# -- Path setup -----------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL

# -- Settings -------------------------------------------------------------------
USER_AGENT          = "Fortis Stock Screener research@fortis.com"
MAX_REQ_PER_SEC     = 8          # SEC ceiling is 10/s -- stay safely under
FEED_COUNT          = 40
REQUEST_TIMEOUT     = 30
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

# -- Logging --------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Rate limiter
# ══════════════════════════════════════════════════════════════════════════════

class _RateLimiter:
    """Blocks so successive calls never exceed max_per_sec."""

    def __init__(self, max_per_sec: int):
        self._min_interval = 1.0 / max_per_sec
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last = time.monotonic()


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _is_13f_season(today: date) -> bool:
    """13F-HR filings are due 45 days after each quarter end, so they cluster
    in Feb / May / Aug / Nov."""
    return today.month in (2, 5, 8, 11)


def _feed_url(form_type: str) -> str:
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcurrent&type={form_type}&company=&dateb=&owner=include"
        f"&count={FEED_COUNT}&output=atom"
    )


def _struct_to_iso(t) -> Optional[str]:
    if t is None:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc).isoformat()
    except Exception:
        return None


# CIK appears in feed titles as "...Company Name (0001234567) (Filer)"
_CIK_RE = re.compile(r"\((\d{4,10})\)")


def _parse_entry(entry, fallback_form: str, cik_to_ticker: dict[int, str]) -> Optional[dict]:
    title = (entry.get("title") or "").strip()
    link  = (entry.get("link") or "").strip()
    if not link:
        return None  # no unique filing URL -- skip

    cik_stored: Optional[str] = None
    ticker: Optional[str] = None
    m = _CIK_RE.search(title)
    if m:
        cik_stored = m.group(1).zfill(10)
        ticker = cik_to_ticker.get(int(m.group(1)))

    # Form type: prefer the ATOM <category> term, fall back to the title prefix
    form_type = None
    tags = entry.get("tags") or []
    if tags and tags[0].get("term"):
        form_type = tags[0]["term"].strip()
    if not form_type:
        form_type = title.split(" - ", 1)[0].strip() if " - " in title else fallback_form

    return {
        "ticker":      ticker,
        "cik":         cik_stored,
        "form_type":   form_type,
        "filing_date": _struct_to_iso(
            entry.get("updated_parsed") or entry.get("published_parsed")
        ),
        "filing_url":  link,
        "description": title or None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Fetchers
# ══════════════════════════════════════════════════════════════════════════════

def _load_cik_ticker_map(session: requests.Session, limiter: _RateLimiter) -> dict[int, str]:
    limiter.wait()
    resp = session.get(COMPANY_TICKERS_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    mapping = {int(row["cik_str"]): row["ticker"] for row in data.values()}
    log.info("Loaded CIK->ticker map: %d companies", len(mapping))
    return mapping


def _fetch_feed(
    session: requests.Session,
    limiter: _RateLimiter,
    form_type: str,
    cik_to_ticker: dict[int, str],
) -> list[dict]:
    limiter.wait()
    try:
        resp = session.get(_feed_url(form_type), timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("Feed fetch failed [%s]: %s", form_type, exc)
        return []

    feed = feedparser.parse(resp.content)
    records = [
        rec
        for entry in feed.entries
        if (rec := _parse_entry(entry, form_type, cik_to_ticker))
    ]
    log.info("Feed %-7s -- %d filings parsed", form_type, len(records))
    return records


# ══════════════════════════════════════════════════════════════════════════════
# Database insert
# ══════════════════════════════════════════════════════════════════════════════

def _store(client, records: list[dict]) -> int:
    """Upsert filings; ON CONFLICT (filing_url) DO NOTHING. Returns new rows."""
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in records:
        url = r["filing_url"]
        if url and url not in seen:
            seen.add(url)
            deduped.append(r)
    if not deduped:
        return 0
    try:
        resp = (
            client.table("sec_filings")
            .upsert(deduped, on_conflict="filing_url", ignore_duplicates=True)
            .execute()
        )
        return len(resp.data) if resp.data else 0
    except Exception as exc:
        log.warning("Insert failed: %s", exc)
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run() -> None:
    today = datetime.now(timezone.utc).date()

    feeds = ["8-K", "4"]
    if _is_13f_season(today):
        feeds.append("13F-HR")
        log.info("13F season (%s) -- including 13F-HR feed", today.strftime("%B"))
    else:
        log.info("Not 13F season -- skipping 13F-HR feed")

    limiter = _RateLimiter(MAX_REQ_PER_SEC)
    session = requests.Session()
    session.headers.update(_HEADERS)

    cik_to_ticker = _load_cik_ticker_map(session, limiter)

    all_records: list[dict] = []
    for form_type in feeds:
        all_records.extend(_fetch_feed(session, limiter, form_type, cik_to_ticker))

    unique_urls = {r["filing_url"] for r in all_records if r["filing_url"]}
    resolved    = sum(1 for r in all_records if r["ticker"])
    log.info(
        "Collected %d filings (%d unique URLs; %d resolved to a ticker).",
        len(all_records), len(unique_urls), resolved,
    )

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    inserted = _store(db, all_records)
    log.info(
        "Done. %d new filings inserted, %d already existed in DB.",
        inserted, len(unique_urls) - inserted,
    )

    # -- Confirmation: show the 10 most recently fetched rows --------------------
    resp = (
        db.table("sec_filings")
        .select("ticker,cik,form_type,filing_date,description")
        .order("fetched_at", desc=True)
        .limit(10)
        .execute()
    )
    if resp.data:
        log.info("")
        log.info("-- 10 most recently fetched filings in sec_filings --")
        log.info("%-7s  %-9s  %-12s  %s", "TICKER", "FORM", "FILED", "DESCRIPTION")
        for row in resp.data:
            log.info(
                "%-7s  %-9s  %-12s  %s",
                row.get("ticker") or "-",
                row.get("form_type") or "-",
                (row.get("filing_date") or "")[:10] or "-",
                (row.get("description") or "")[:62],
            )


if __name__ == "__main__":
    run()
