"""
News Harvester Agent
Fetches the last 24 h of news for every ticker in tickers.csv from three
sources (Yahoo Finance RSS, Google News RSS, Finnhub), deduplicates by URL,
and bulk-inserts into the Supabase news_items table.

Usage:
    python pipeline/agents/news_harvester.py
"""

import asyncio
import calendar
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import feedparser
import pandas as pd
from supabase import create_client

# ── Path setup ─────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    FINNHUB_API_KEY,
    SUPABASE_SERVICE_KEY,
    SUPABASE_URL,
    TICKER_UNIVERSE_PATH,
)

# ── Settings ───────────────────────────────────────────────────────────────────
MAX_CONCURRENCY    = 20
MAX_AGE_HOURS      = 24
INSERT_BATCH_SIZE  = 200
TIMEOUT            = aiohttp.ClientTimeout(total=15)
SUMMARY_MAX_CHARS  = 500

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()[:SUMMARY_MAX_CHARS]


def _struct_time_to_dt(t) -> Optional[datetime]:
    """feedparser returns published_parsed as UTC struct_time."""
    if t is None:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    except Exception:
        return None


def _is_recent(dt: Optional[datetime], cutoff: datetime) -> bool:
    """Keep articles with no date (can't rule them out) and recent ones."""
    return dt is None or dt >= cutoff


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# ══════════════════════════════════════════════════════════════════════════════
# Per-source fetchers
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_yahoo(
    session: aiohttp.ClientSession, ticker: str, cutoff: datetime
) -> list[dict]:
    url = (
        f"https://feeds.finance.yahoo.com/rss/2.0/headline"
        f"?s={ticker}&region=US&lang=en-US"
    )
    try:
        async with session.get(url, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return []
            raw = await resp.text()
        feed = feedparser.parse(raw)
        out = []
        for e in feed.entries:
            dt = _struct_time_to_dt(e.get("published_parsed"))
            if not _is_recent(dt, cutoff):
                continue
            headline = _strip_html(e.get("title", ""))
            link     = e.get("link", "")
            if not headline or not link:
                continue
            out.append({
                "ticker":       ticker,
                "headline":     headline,
                "url":          link,
                "source":       "yahoo_finance",
                "published_at": _to_iso(dt),
                "summary":      _strip_html(e.get("summary", "")),
            })
        return out
    except Exception as exc:
        log.debug("Yahoo RSS error [%s]: %s", ticker, exc)
        return []


async def _fetch_google(
    session: aiohttp.ClientSession, ticker: str, cutoff: datetime
) -> list[dict]:
    url = (
        f"https://news.google.com/rss/search"
        f"?q={ticker}+stock+when:1d&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        async with session.get(url, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return []
            raw = await resp.text()
        feed = feedparser.parse(raw)
        out = []
        for e in feed.entries:
            dt = _struct_time_to_dt(e.get("published_parsed"))
            if not _is_recent(dt, cutoff):
                continue
            headline = _strip_html(e.get("title", ""))
            link     = e.get("link", "")
            if not headline or not link:
                continue
            out.append({
                "ticker":       ticker,
                "headline":     headline,
                "url":          link,
                "source":       "google_news",
                "published_at": _to_iso(dt),
                "summary":      _strip_html(e.get("summary", "")),
            })
        return out
    except Exception as exc:
        log.debug("Google News RSS error [%s]: %s", ticker, exc)
        return []


async def _fetch_finnhub(
    session: aiohttp.ClientSession, ticker: str, cutoff: datetime
) -> list[dict]:
    if not FINNHUB_API_KEY:
        return []
    now   = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    url = (
        f"https://finnhub.io/api/v1/company-news"
        f"?symbol={ticker}&from={yesterday}&to={today}&token={FINNHUB_API_KEY}"
    )
    try:
        async with session.get(url, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            ts = item.get("datetime")
            dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            if not _is_recent(dt, cutoff):
                continue
            headline = _strip_html(item.get("headline", ""))
            link     = item.get("url", "")
            if not headline or not link:
                continue
            out.append({
                "ticker":       ticker,
                "headline":     headline,
                "url":          link,
                "source":       "finnhub",
                "published_at": _to_iso(dt),
                "summary":      _strip_html(item.get("summary", "")),
            })
        return out
    except Exception as exc:
        log.debug("Finnhub error [%s]: %s", ticker, exc)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Per-ticker orchestration
# ══════════════════════════════════════════════════════════════════════════════

async def _process_ticker(
    session: aiohttp.ClientSession,
    ticker: str,
    semaphore: asyncio.Semaphore,
    cutoff: datetime,
) -> list[dict]:
    async with semaphore:
        raw: list[dict] = []
        for fetcher in (_fetch_yahoo, _fetch_google, _fetch_finnhub):
            try:
                raw.extend(await fetcher(session, ticker, cutoff))
            except Exception as exc:
                log.debug("Unhandled error in %s [%s]: %s", fetcher.__name__, ticker, exc)

        # Deduplicate by URL within this ticker
        seen: set[str] = set()
        deduped = []
        for a in raw:
            if a["url"] not in seen:
                seen.add(a["url"])
                deduped.append(a)
        return deduped


# ══════════════════════════════════════════════════════════════════════════════
# Database insert
# ══════════════════════════════════════════════════════════════════════════════

def _bulk_insert(client, articles: list[dict]) -> int:
    """
    Insert articles in batches of INSERT_BATCH_SIZE.
    ON CONFLICT (url) DO NOTHING via ignore_duplicates=True.
    Returns the number of rows actually inserted (new, not pre-existing).
    """
    inserted = 0
    for i in range(0, len(articles), INSERT_BATCH_SIZE):
        batch = articles[i : i + INSERT_BATCH_SIZE]
        try:
            resp = (
                client.table("news_items")
                .upsert(batch, on_conflict="url", ignore_duplicates=True)
                .execute()
            )
            inserted += len(resp.data) if resp.data else 0
        except Exception as exc:
            log.warning("Batch insert failed (rows %d-%d): %s", i, i + len(batch), exc)
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

async def run() -> None:
    tickers: list[str] = (
        pd.read_csv(TICKER_UNIVERSE_PATH)["ticker"].dropna().astype(str).tolist()
    )
    total = len(tickers)
    log.info("Loaded %d tickers — starting news harvest (max %d concurrent)", total, MAX_CONCURRENCY)

    cutoff    = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    db        = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    all_articles: list[dict] = []
    processed = 0

    async with aiohttp.ClientSession(headers=_HEADERS) as session:
        tasks = [_process_ticker(session, t, semaphore, cutoff) for t in tickers]
        for coro in asyncio.as_completed(tasks):
            articles = await coro
            all_articles.extend(articles)
            processed += 1
            if processed % 100 == 0 or processed == total:
                log.info(
                    "Processed %d/%d tickers, collected %d articles so far",
                    processed, total, len(all_articles),
                )

    # Global deduplication across all tickers (same URL cited for multiple tickers)
    seen_global: set[str] = set()
    unique_articles: list[dict] = []
    for a in all_articles:
        if a["url"] not in seen_global:
            seen_global.add(a["url"])
            unique_articles.append(a)

    cross_dupes = len(all_articles) - len(unique_articles)
    if cross_dupes:
        log.info("Removed %d cross-ticker duplicate URLs", cross_dupes)

    log.info("Inserting %d unique articles into news_items…", len(unique_articles))
    inserted = _bulk_insert(db, unique_articles)

    log.info(
        "Done. %d new articles inserted, %d already existed in DB.",
        inserted,
        len(unique_articles) - inserted,
    )


if __name__ == "__main__":
    # aiohttp requires SelectorEventLoop on Windows (Python 3.8+)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
