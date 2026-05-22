"""
IR RSS Fetcher  --  Tier 3 of the transcript fallback hierarchy.

Most public companies publish an investor-relations RSS feed. When a company
posts its earnings-call transcript, that transcript is linked from the feed.
This module *subscribes* to those feeds (it does not scrape IR sites) and, when
a transcript entry is found near the relevant quarter, fetches and parses it.

Tier 3 sits between the SEC 8-K paths (Tier 1/2) and audio transcription
(Tier 4): it is cheap and exact when it works, so it is tried before the slow
audio path. Tickers without a verified feed in data/ir_rss_feeds.csv simply
skip Tier 3 -- no guessing.

Public entry point (called by sec_transcript_fetcher PATH 3):
    find_ir_rss_transcript(ticker, year, quarter) -> dict | None

CLI:
    python pipeline/agents/ir_rss_fetcher.py AAPL 2026 1
"""

from __future__ import annotations

import csv
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.audio_transcriber import IR_USER_AGENT, _http_get, _robots_ok  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent          # pipeline/
IR_RSS_CSV = ROOT / "data" / "ir_rss_feeds.csv"

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Feed lookup
# ══════════════════════════════════════════════════════════════════════════════

def _load_rss_feeds() -> dict[str, str]:
    """Parse the hand-maintained ir_rss_feeds.csv seed list -> {ticker: url}."""
    out: dict[str, str] = {}
    if not IR_RSS_CSV.is_file():
        return out
    try:
        with IR_RSS_CSV.open(encoding="utf-8") as fh:
            data_lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
        for row in csv.DictReader(data_lines):
            t = (row.get("ticker") or "").strip().upper()
            url = (row.get("rss_url") or "").strip()
            if t and url:
                out[t] = url
    except Exception as exc:
        log.warning("failed to read %s: %s", IR_RSS_CSV, exc)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Feed parsing + entry selection
# ══════════════════════════════════════════════════════════════════════════════

def _parse_feed(url: str) -> list:
    try:
        import feedparser
        parsed = feedparser.parse(url, agent=IR_USER_AGENT)
        return list(parsed.entries or [])
    except Exception as exc:
        log.warning("RSS parse failed for %s: %s", url, exc)
        return []


def _entry_date(entry) -> date | None:
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            try:
                return date(tm.tm_year, tm.tm_mon, tm.tm_mday)
            except Exception:
                continue
    return None


def _select_transcript_entry(entries: list, year: int, quarter: int):
    """Pick the feed entry most likely to be this quarter's earnings-call
    transcript. Conservative: returns None rather than a wrong-quarter match."""
    from agents.sec_transcript_fetcher import _quarter_window

    win_start, win_end = _quarter_window(year, quarter)
    q_tokens = (f"q{quarter}", f"q{quarter} {year}", f"{quarter}q",
                f"q{quarter}'", f"q{quarter}-{year}")

    best, best_score = None, 0
    for e in entries:
        title = e.get("title") or ""
        summary = e.get("summary") or e.get("description") or ""
        blob = f"{title} {summary}".lower()
        if "transcript" not in blob:
            continue
        if not any(w in blob for w in ("earnings", "call", "conference")):
            continue

        score = 1
        d = _entry_date(e)
        if d is not None:
            if d < win_start or d > win_end:
                continue                       # outside this quarter's window
            score += 2
        if any(tok in blob for tok in q_tokens):
            score += 3
        if str(year) in blob:
            score += 1
        if score > best_score:
            best, best_score = e, score
    return best


# ══════════════════════════════════════════════════════════════════════════════
# Transcript-page fetch
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_transcript_page(url: str) -> dict | None:
    """Fetch and extract text from a transcript page linked by the feed."""
    from agents.sec_transcript_fetcher import _parse_pdf_transcript, _strip_html

    r = _http_get(url, timeout=45)
    if r is None or r.status_code != 200:
        log.info("IR-feed transcript page fetch failed (%s)", url)
        return None
    ctype = (r.headers.get("Content-Type") or "").lower()
    if url.lower().endswith(".pdf") or "pdf" in ctype:
        return _parse_pdf_transcript(url, r.content)
    return {"text": _strip_html(r.text), "format": "html", "source_url": url}


# ══════════════════════════════════════════════════════════════════════════════
# PATH 3 entry point
# ══════════════════════════════════════════════════════════════════════════════

def find_ir_rss_transcript(ticker: str, year: int, quarter: int,
                           earnings_date: str | None = None) -> dict | None:
    """Tier-3 fallback: a transcript posted to the company IR RSS feed.

    Returns the standard transcript dict (source_path = 'ir_rss_transcript')
    or None if there is no verified feed / no transcript entry / the linked
    page does not validate as a real transcript."""
    ticker = ticker.upper()
    feed_url = _load_rss_feeds().get(ticker)
    if not feed_url:
        log.info("%s: no IR RSS feed in lookup -- skipping Tier 3", ticker)
        return None

    entries = _parse_feed(feed_url)
    if not entries:
        log.info("%s: IR RSS feed empty or unreachable", ticker)
        return None

    entry = _select_transcript_entry(entries, year, quarter)
    if entry is None:
        log.info("%s: no transcript entry in IR feed for %dQ%d",
                 ticker, year, quarter)
        return None

    link = entry.get("link")
    if not link:
        return None
    if not _robots_ok(link):
        log.info("%s: robots.txt disallows %s -- skipping Tier 3", ticker, link)
        return None

    parsed = _fetch_transcript_page(link)
    if not parsed or not parsed.get("text"):
        return None

    from agents.sec_transcript_fetcher import (structure_full_transcript,
                                               validate_transcript_quality)
    text = parsed["text"]
    segments = structure_full_transcript(text)
    quality = validate_transcript_quality(text, segments)
    if quality not in ("full_call_with_qa", "full_call_prepared_only"):
        log.info("%s: IR-feed page did not validate as a transcript (%s)",
                 ticker, quality)
        return None

    d = _entry_date(entry)
    log.info("%s %dQ%d: transcript found via IR RSS feed -> %s",
             ticker, year, quarter, quality)
    return {
        "transcript_text": text,
        "structured_segments": [s.to_dict() for s in segments],
        "quality_grade": quality,
        "source_url": link,
        "source_path": "ir_rss_transcript",
        "earnings_date": d.isoformat() if d else earnings_date,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def _main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")
    if len(sys.argv) < 4:
        print("usage: ir_rss_fetcher.py TICKER YEAR QUARTER")
        sys.exit(2)
    r = find_ir_rss_transcript(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
    if r is None:
        print("\nNo transcript found via the IR RSS feed.")
        return
    print(f"\nQUALITY:     {r['quality_grade']}")
    print(f"SOURCE URL:  {r['source_url']}")
    print(f"SEGMENTS:    {len(r['structured_segments'])}")
    print(f"TEXT LENGTH: {len(r['transcript_text']):,} chars")


if __name__ == "__main__":
    _main()
