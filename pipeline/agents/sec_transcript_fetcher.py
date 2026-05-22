"""
SEC Transcript Fetcher  (Step 6.7 upgrade)
Pulls full earnings call transcripts directly from SEC EDGAR 8-K exhibits
-- the authoritative, permanently-free source. Bloomberg and FactSet
ultimately rely on the same filings. Replaces the paywalled API Ninjas path.

ARCHITECTURE

  PATH 1 -- 8-K exhibit transcript
    For each 8-K filing in the relevant window, fetch the filing index;
    inspect every exhibit; pick the one most likely to be a transcript
    (EX-99.2, or any exhibit whose filename / description contains
    transcript / conference call / earnings call keywords). Parse HTML,
    PDF, or TXT depending on format. Validate by checking transcript
    markers (Operator/Q&A/multiple speakers/length).

  PATH 2 -- Item 7.01 standalone filing
    Some companies file the transcript 24-48 hours AFTER the press
    release as a separate 8-K under Item 7.01 (Reg FD). We look at all
    8-Ks in [earnings_date, earnings_date + 5 days].

  PATH 3 -- IR page scrape (fragile; opt-in)
    Some companies post transcripts on investor relations pages without
    filing them. We try yfinance to find the IR URL, look for a transcripts
    page, and grab the most recent. Many sites have anti-bot protection so
    this is graceful-degrade only.

  PATH 4 -- Press release fallback
    If nothing usable, we use the 8-K Item 2.02 description from sec_filings.
    Quality is marked 'press_release_only' so downstream signal strength
    is capped at 50.

RATE LIMITING
  SEC's polite limit is 10 req/s with a User-Agent header. We track the last
  10 request timestamps and sleep to maintain the cap; 429 responses
  trigger exponential backoff starting at 30s up to 5 min.

CACHING
  Transcripts don't change after publication. Two layers:
    * On-disk JSON cache at pipeline/data/transcripts/{ticker}_{year}_Q{q}.json
      (also used by smart_money_intel.py if the agent is offline-mode)
    * Supabase table earnings_transcripts (cross-pipeline persistence)
  Both are checked before any SEC request. 30-day TTL on the disk cache;
  the DB cache is effectively permanent (re-fetch only on demand).

Usage:
    from agents.sec_transcript_fetcher import fetch_full_transcript
    r = fetch_full_transcript("AAPL", 2026, 1)
    # -> {"transcript_text": "...", "structured_segments": [...],
    #     "quality_grade": "full_call", "source_url": "...",
    #     "source_path": "sec_8k_item202", "earnings_date": "2026-04-30"}

    python pipeline/agents/sec_transcript_fetcher.py AAPL 2026 1
        Single-fetch CLI for debugging.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "transcripts"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

EDGAR_UA = ("Fortis Stock Intelligence research@fortisagency.test "
            "(institutional research)")
HTTP_TIMEOUT = 45
DISK_CACHE_TTL_DAYS = 30
DEFAULT_LOOKBACK_DAYS = 100   # covers up to last 3+ months of 8-K filings

# Rate-limit knobs
MAX_REQUESTS_PER_SECOND = 8           # SEC says 10/s; we leave headroom
BACKOFF_INITIAL_SECONDS = 30
BACKOFF_MAX_SECONDS     = 300

# Transcript validation thresholds
MIN_FULL_CALL_WORDS  = 3000
MIN_QA_PAIRS         = 5
MIN_SPEAKER_TURNS    = 12

TRANSCRIPT_FILENAME_HINTS = (
    "transcript", "earnings", "conference", "call", "q&a",
    "q1", "q2", "q3", "q4", "fy",
)
PRESS_RELEASE_HINTS = (
    "press-release", "press_release", "earnings-release", "earnings_release",
    "results", "announces",
)

SPEAKER_TURN_RE = re.compile(
    # "Name [-- ] Title:" or "Name -- Title", or just "Operator:"
    r"^\s*([A-Z][A-Za-z.\-'\s]{1,60})\s*[\-:–—]+\s*([A-Z][A-Za-z\s,&'.\-/]{1,80})?\s*$",
    re.MULTILINE,
)
OPERATOR_RE   = re.compile(r"^\s*(?:Operator|Coordinator|Moderator)\s*[:\-–—]",
                             re.MULTILINE | re.IGNORECASE)
QA_HEADER_RE  = re.compile(r"(?:question\s*[\- ]and\s*[\- ]answer|q\s*&\s*a|q&a session)",
                             re.IGNORECASE)
ANALYST_Q_RE  = re.compile(r"(?:my question is|just curious|could you (?:elaborate|comment)|"
                             r"thanks for taking|just on)", re.IGNORECASE)

# Quarter-to-filing-month heuristic. Earnings calls typically happen ~30-45 days
# after fiscal quarter end. We pick a generous window so we don't miss off-cycle
# fiscal-year companies.
QUARTER_FILING_WINDOWS = {
    1: ((4, 1),  (7, 31)),     # Q1 reports typically Apr-Jul
    2: ((7, 1),  (10, 31)),    # Q2 reports Jul-Oct
    3: ((10, 1), (1, 31)),     # Q3 reports Oct-Jan (next year)
    4: ((1, 1),  (4, 30)),     # Q4 reports Jan-Apr (next year)
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Rate limiter (sliding window, thread-unsafe but we run sync)
# ══════════════════════════════════════════════════════════════════════════════

class SecRateLimiter:
    def __init__(self, max_per_second: int = MAX_REQUESTS_PER_SECOND):
        self.max_per_second = max_per_second
        self.window: deque[float] = deque()
        self.backoff_until: float = 0.0
        self.rate_limit_hits = 0

    def wait_for_slot(self):
        now = time.monotonic()
        if now < self.backoff_until:
            sleep = self.backoff_until - now
            log.info("SEC backoff: sleeping %.1fs", sleep)
            time.sleep(sleep)
            now = time.monotonic()
        # Drop entries > 1s old
        while self.window and now - self.window[0] > 1.0:
            self.window.popleft()
        if len(self.window) >= self.max_per_second:
            sleep = 1.0 - (now - self.window[0]) + 0.01
            if sleep > 0:
                time.sleep(sleep)
        self.window.append(time.monotonic())

    def trigger_backoff(self):
        self.rate_limit_hits += 1
        seconds = min(BACKOFF_MAX_SECONDS,
                      BACKOFF_INITIAL_SECONDS * (2 ** (self.rate_limit_hits - 1)))
        self.backoff_until = time.monotonic() + seconds
        log.warning("SEC 429 -- backing off for %ds (hit #%d)",
                    seconds, self.rate_limit_hits)


_RATE_LIMITER = SecRateLimiter()


def _http_get(url: str, headers: dict | None = None,
               timeout: int = HTTP_TIMEOUT, binary: bool = False):
    _RATE_LIMITER.wait_for_slot()
    h = {"User-Agent": EDGAR_UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, headers=h, timeout=timeout)
    except requests.RequestException as exc:
        log.debug("http GET %s failed: %s", url, exc)
        return None
    if r.status_code == 429:
        _RATE_LIMITER.trigger_backoff()
        return None
    return r


# ══════════════════════════════════════════════════════════════════════════════
# Tiny helpers
# ══════════════════════════════════════════════════════════════════════════════

def _to_date(s) -> date | None:
    if s is None:
        return None
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except Exception:
        return None


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _quarter_window(year: int, quarter: int) -> tuple[date, date]:
    """Date range to search for earnings filings for (year, quarter)."""
    (sm, sd), (em, ed) = QUARTER_FILING_WINDOWS[quarter]
    end_year = year + 1 if quarter in (3, 4) else year
    start = date(year, sm, sd)
    end   = date(end_year, em, ed)
    return start, end


def _resolve_cik(ticker: str) -> str | None:
    cache = ROOT / "data" / "sec_cik_map.json"
    if cache.exists():
        try:
            mp = json.loads(cache.read_text(encoding="utf-8"))
            if ticker in mp:
                return mp[ticker]
        except Exception:
            mp = {}
    else:
        mp = {}
    r = _http_get("https://www.sec.gov/files/company_tickers.json")
    if not r or r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    for v in data.values():
        if (v.get("ticker") or "").upper() == ticker.upper():
            mp[ticker] = str(v["cik_str"]).zfill(10)
            cache.write_text(json.dumps(mp), encoding="utf-8")
            return mp[ticker]
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 8-K filing discovery
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class FilingMeta:
    accession: str
    filed:     date
    form:      str
    primary_doc: str | None = None
    items:     str | None = None          # "2.02,9.01" etc, if known

    @property
    def acc_clean(self) -> str:
        return self.accession.replace("-", "")


def find_8k_transcript_filings(ticker: str, year: int, quarter: int,
                                 lookback_days: int = DEFAULT_LOOKBACK_DAYS
                                 ) -> list[FilingMeta]:
    """Return all 8-K filings that overlap the quarter's earnings window.
    We don't pre-filter by Item number here -- the exhibit-level filter is
    more reliable since Item parsing requires fetching the primary document."""
    cik = _resolve_cik(ticker)
    if not cik:
        return []
    r = _http_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if not r or r.status_code != 200:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    recent = (data.get("filings") or {}).get("recent") or {}
    forms       = recent.get("form")           or []
    accs        = recent.get("accessionNumber") or []
    filed       = recent.get("filingDate")     or []
    items_list  = recent.get("items")          or []
    primary     = recent.get("primaryDocument") or []
    win_start, win_end = _quarter_window(year, quarter)
    # Be generous on both sides
    win_start -= timedelta(days=14)
    win_end   += timedelta(days=14)

    out = []
    for i, form in enumerate(forms):
        if form not in ("8-K", "8-K/A"):
            continue
        d = _to_date(filed[i] if i < len(filed) else None)
        if d is None or d < win_start or d > win_end:
            continue
        out.append(FilingMeta(
            accession=accs[i] if i < len(accs) else "",
            filed=d,
            form=form,
            primary_doc=primary[i] if i < len(primary) else None,
            items=items_list[i] if i < len(items_list) else None,
        ))
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Filing-level exhibit discovery
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Exhibit:
    name: str
    type: str
    url: str
    description: str | None = None
    score: float = 0.0      # transcript likelihood (filled below)


def _score_exhibit(ex: Exhibit) -> float:
    """Heuristic: probability that this exhibit is a transcript.
    Description fields are trusted MORE than filenames or exhibit numbers
    because companies are increasingly inconsistent about EX-99.x usage."""
    n = (ex.name or "").lower()
    t = (ex.type or "").lower()
    d = (ex.description or "").lower()
    score = 0.0

    # Description hints (highest signal)
    if "transcript" in d:
        score += 0.7
    elif "conference call" in d or "earnings call" in d:
        score += 0.45
    elif "press release" in d or "earnings release" in d:
        score -= 0.3   # explicit press release

    # Exhibit number conventions
    if "99.2" in t or "ex-99.2" in t:
        score += 0.4
    elif "99.1" in t or "ex-99.1" in t:
        # Press release slot by convention -- mild prior unless desc disagrees
        score -= 0.1

    # Filename hints
    if any(h in n for h in TRANSCRIPT_FILENAME_HINTS) and "press" not in n:
        score += 0.18
    if any(h in n for h in PRESS_RELEASE_HINTS):
        score -= 0.2

    return max(0.0, min(1.0, score))


def get_filing_exhibits(accession: str, cik: str) -> list[Exhibit]:
    """Parse the filing's human-readable index HTML to extract proper
    exhibit metadata (EX-99.1, EX-99.2, descriptions). The index.json `type`
    field is just a file icon name; the real exhibit numbering is only in
    the index HTML."""
    acc_clean = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}"
    # Human-readable index HTML
    index_url = f"{base}/{accession}-index.html"
    r = _http_get(index_url)
    if not r or r.status_code != 200:
        # Fall back to bare directory listing
        return _exhibits_from_dir_json(accession, cik, base)

    # Parse the index HTML's document table. SEC renders it as:
    #   <table summary="Document Format Files"><tr><th>Seq</th><th>Description</th>
    #     <th>Document</th><th>Type</th><th>Size</th></tr>
    #   <tr><td>1</td><td>...</td><td><a href="d8k.htm">d8k.htm</a></td>
    #     <td>EX-99.2</td><td>...</td></tr>...
    soup = BeautifulSoup(r.text, "lxml")
    exhibits = []
    seen_names = set()
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower()
                   for th in table.find_all("th")]
        if not headers or "type" not in headers:
            continue
        try:
            idx_desc = headers.index("description")
            idx_doc  = headers.index("document")
            idx_type = headers.index("type")
        except ValueError:
            continue
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= max(idx_desc, idx_doc, idx_type):
                continue
            desc = cells[idx_desc].get_text(strip=True)
            type_label = cells[idx_type].get_text(strip=True)
            anchor = cells[idx_doc].find("a")
            if not anchor:
                continue
            name = anchor.get_text(strip=True)
            href = anchor.get("href") or ""
            if href.startswith("/"):
                url = f"https://www.sec.gov{href}"
            elif href.startswith("http"):
                url = href
            else:
                url = f"{base}/{name}"
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            ex = Exhibit(name=name, type=type_label, url=url,
                          description=desc or None)
            ex.score = _score_exhibit(ex)
            exhibits.append(ex)
    if not exhibits:
        return _exhibits_from_dir_json(accession, cik, base)
    exhibits.sort(key=lambda e: e.score, reverse=True)
    return exhibits


def _exhibits_from_dir_json(accession: str, cik: str, base: str) -> list[Exhibit]:
    """Fallback when the HTML index can't be parsed. The directory JSON has
    file metadata but lacks proper exhibit types -- we can still score by
    filename hints."""
    r = _http_get(f"{base}/index.json")
    if not r or r.status_code != 200:
        return []
    try:
        items = r.json().get("directory", {}).get("item", []) or []
    except ValueError:
        return []
    exhibits = []
    for it in items:
        name = it.get("name") or ""
        if not name:
            continue
        if not (name.endswith(".htm") or name.endswith(".html")
                or name.endswith(".pdf") or name.endswith(".txt")):
            continue
        if "index" in name.lower() or name.startswith(accession.split("-")[0]):
            continue
        ex = Exhibit(name=name, type="", url=f"{base}/{name}",
                      description=None)
        ex.score = _score_exhibit(ex)
        exhibits.append(ex)
    exhibits.sort(key=lambda e: e.score, reverse=True)
    return exhibits


# ══════════════════════════════════════════════════════════════════════════════
# Format-specific parsers
# ══════════════════════════════════════════════════════════════════════════════

def _strip_html(html: str) -> str:
    """Clean text extraction from a SEC HTML exhibit. We remove only true
    noise (script/style/head/form). Tables are preserved -- many earnings
    press releases (esp. Workiva-generated) use tables for layout and
    contain the financial data we need."""
    soup = BeautifulSoup(html, "lxml")
    # Kill genuine noise; leave tables alone (they hold financial content)
    for sel in ("script", "style", "head", "form", "nav", "noscript"):
        for el in soup.find_all(sel):
            el.decompose()
    # SEC sometimes wraps the main doc in a <body>. If a <main>/<article>
    # exists, prefer it.
    target = soup.find("article") or soup.find("main") or soup.body or soup
    text = target.get_text(separator="\n")
    # Compress runs of blank lines to one
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    # Strip leading/trailing whitespace per line
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text


def _parse_html_transcript(url: str, html: str) -> dict:
    text = _strip_html(html)
    return {"text": text, "format": "html", "source_url": url}


def _parse_pdf_transcript(url: str, pdf_bytes: bytes) -> dict | None:
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n\n".join(p for p in pages if p)
        if _word_count(text) < 200:
            # Likely a scanned/image PDF -- OCR needed; skip
            return None
        return {"text": text, "format": "pdf", "source_url": url}
    except Exception as exc:
        log.debug("PDF parse failed for %s: %s", url, exc)
        return None


def _parse_txt_transcript(url: str, text: str) -> dict:
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    return {"text": text.strip(), "format": "txt", "source_url": url}


def fetch_and_parse_exhibit(exhibit: Exhibit) -> dict | None:
    """Returns {text, format, source_url} or None on failure."""
    r = _http_get(exhibit.url)
    if not r or r.status_code != 200:
        return None
    name_lower = exhibit.name.lower()
    ctype = (r.headers.get("Content-Type") or "").lower()
    if name_lower.endswith(".pdf") or "pdf" in ctype:
        return _parse_pdf_transcript(exhibit.url, r.content)
    if (name_lower.endswith(".htm") or name_lower.endswith(".html")
            or "html" in ctype):
        return _parse_html_transcript(exhibit.url, r.text)
    if name_lower.endswith(".txt") or "text/plain" in ctype:
        return _parse_txt_transcript(exhibit.url, r.text)
    # Best-effort -- try html since most SEC docs are
    return _parse_html_transcript(exhibit.url, r.text)


# ══════════════════════════════════════════════════════════════════════════════
# Structuring + validation
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Segment:
    role: str
    name: str
    text: str
    segment_type: str    # prepared_remarks | qa_question | qa_answer | operator

    def to_dict(self) -> dict:
        return {"role": self.role, "name": self.name, "text": self.text,
                "segment_type": self.segment_type}


def _split_speaker_turns(text: str) -> list[tuple[str, str, str]]:
    """Return list of (speaker, title_if_any, body). Lines that look like
    'Name - Title' on their own line are speaker markers; everything until
    the next marker is that speaker's body."""
    lines = text.split("\n")
    turns: list[tuple[str, str, list[str]]] = []
    cur_speaker: str | None = None
    cur_title:   str = ""
    cur_body:    list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            if cur_body:
                cur_body.append("")
            continue
        m = SPEAKER_TURN_RE.match(line)
        # Speaker markers are usually short
        is_marker = (m is not None and len(line) < 120)
        # Avoid matching ordinary sentences -- speaker name should be
        # 2-5 capitalized words
        if is_marker:
            speaker = m.group(1).strip()
            words = speaker.split()
            if not (1 <= len(words) <= 5):
                is_marker = False
            elif not all(w[:1].isupper() for w in words if w):
                is_marker = False
        if is_marker:
            if cur_speaker is not None:
                turns.append((cur_speaker, cur_title,
                              "\n".join(cur_body).strip()))
            cur_speaker = m.group(1).strip()
            cur_title   = (m.group(2) or "").strip()
            cur_body    = []
        else:
            if cur_speaker is not None:
                cur_body.append(line)
    if cur_speaker is not None and cur_body:
        turns.append((cur_speaker, cur_title, "\n".join(cur_body).strip()))
    return [(s, t, b) for s, t, b in turns if b]


def _classify_role(name: str, title: str) -> str:
    n = (name or "").lower()
    t = (title or "").lower()
    if "operator" in n or "coordinator" in n or "moderator" in n:
        return "Operator"
    if "chief executive" in t or " ceo" in f" {t}" or t == "ceo":
        return "CEO"
    if "chief financial" in t or " cfo" in f" {t}" or t == "cfo":
        return "CFO"
    if "chief operating" in t or " coo" in f" {t}" or t == "coo":
        return "COO"
    if "investor relations" in t or "ir" in t.split():
        return "IR"
    if "analyst" in t or "research" in t:
        return "Analyst"
    if "vice president" in t or "vp" in t or "president" in t:
        return "Other Officer"
    return "Speaker"


def structure_full_transcript(text: str) -> list[Segment]:
    """Returns structured Segments. We segment by speaker turn, mark Q&A
    region after the QA_HEADER marker, then classify roles and segment
    types from the marker context."""
    turns = _split_speaker_turns(text)
    if not turns:
        return []

    # Find the index where Q&A starts -- first turn AFTER the QA_HEADER marker
    qa_start_idx = None
    full_lower = text.lower()
    qa_marker_pos = -1
    qa_marker_match = QA_HEADER_RE.search(text)
    if qa_marker_match:
        qa_marker_pos = qa_marker_match.start()

    # Walk turns and find their approximate position in the original text
    cumulative = 0
    for i, (speaker, title, body) in enumerate(turns):
        idx = text.find(body[:80], cumulative) if body else -1
        if idx >= 0:
            cumulative = idx + len(body)
            if qa_marker_pos > 0 and idx > qa_marker_pos and qa_start_idx is None:
                qa_start_idx = i

    segs: list[Segment] = []
    last_role = None
    for i, (speaker, title, body) in enumerate(turns):
        role = _classify_role(speaker, title)
        in_qa = qa_start_idx is not None and i >= qa_start_idx
        if role == "Operator":
            seg_type = "operator"
        elif in_qa:
            seg_type = "qa_question" if role == "Analyst" else "qa_answer"
        else:
            seg_type = "prepared_remarks"
        segs.append(Segment(role=role, name=speaker, text=body,
                             segment_type=seg_type))
        last_role = role
    return segs


def validate_transcript_quality(text: str, segments: list[Segment]) -> str:
    """Return 'full_call' | 'partial' | 'press_release_only' | 'unavailable'."""
    if not text or _word_count(text) < 500:
        return "unavailable"
    has_operator = bool(OPERATOR_RE.search(text))
    has_qa_marker = bool(QA_HEADER_RE.search(text))
    speakers = {s.name for s in segments}
    qa_pairs = sum(1 for s in segments if s.segment_type == "qa_question")
    n_turns = len(segments)
    wc = _word_count(text)

    if (wc >= MIN_FULL_CALL_WORDS and n_turns >= MIN_SPEAKER_TURNS
            and (has_qa_marker or qa_pairs >= MIN_QA_PAIRS)
            and len(speakers) >= 4):
        return "full_call"
    if wc >= 1500 and (n_turns >= 5 or has_operator):
        return "partial"
    if wc >= 300:
        return "press_release_only"
    return "unavailable"


# ══════════════════════════════════════════════════════════════════════════════
# Cache (disk + DB)
# ══════════════════════════════════════════════════════════════════════════════

def _cache_path(ticker: str, year: int, quarter: int) -> Path:
    return CACHE_DIR / f"{ticker}_{year}_Q{quarter}.json"


def _read_disk_cache(ticker: str, year: int, quarter: int) -> dict | None:
    p = _cache_path(ticker, year, quarter)
    if not p.exists():
        return None
    age_d = (time.time() - p.stat().st_mtime) / 86400
    if age_d > DISK_CACHE_TTL_DAYS:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_disk_cache(ticker: str, year: int, quarter: int, payload: dict):
    p = _cache_path(ticker, year, quarter)
    try:
        p.write_text(json.dumps(payload, default=str), encoding="utf-8")
    except Exception as exc:
        log.debug("disk cache write failed for %s: %s", p, exc)


def _db():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _read_db_cache(db, ticker: str, year: int, quarter: int) -> dict | None:
    try:
        rows = (db.table("earnings_transcripts").select("*")
                .eq("ticker", ticker)
                .eq("fiscal_year", year)
                .eq("fiscal_quarter", quarter)
                .limit(1).execute().data or [])
    except Exception:
        return None
    if not rows:
        return None
    r = rows[0]
    return {
        "ticker":               r["ticker"],
        "fiscal_year":          r["fiscal_year"],
        "fiscal_quarter":       r["fiscal_quarter"],
        "earnings_date":        r.get("earnings_date"),
        "transcript_text":      r.get("transcript_text") or "",
        "structured_segments":  r.get("structured_segments") or [],
        "quality_grade":        r.get("quality_grade") or "unavailable",
        "source_url":           r.get("source_url"),
        "source_path":          r.get("source_path"),
        "fetched_at":           r.get("fetched_at"),
    }


def _write_db_cache(db, payload: dict):
    try:
        db.table("earnings_transcripts").upsert(
            {
                "ticker":              payload["ticker"],
                "fiscal_year":         payload["fiscal_year"],
                "fiscal_quarter":      payload["fiscal_quarter"],
                "earnings_date":       payload.get("earnings_date"),
                "transcript_text":     payload.get("transcript_text"),
                "structured_segments": payload.get("structured_segments"),
                "quality_grade":       payload.get("quality_grade"),
                "source_url":          payload.get("source_url"),
                "source_path":         payload.get("source_path"),
            },
            on_conflict="ticker,fiscal_year,fiscal_quarter",
        ).execute()
    except Exception as exc:
        log.warning("earnings_transcripts upsert failed for %s: %s",
                    payload.get("ticker"), exc)


def _write_metrics(db, ticker: str, year: int, quarter: int,
                     success: bool, quality: str, path: str | None,
                     attempts: list[dict], elapsed: float):
    try:
        db.table("transcript_fetch_metrics").insert({
            "ticker":              ticker,
            "fiscal_year":         year,
            "fiscal_quarter":      quarter,
            "success":             success,
            "final_quality_grade": quality,
            "path_succeeded":      path,
            "attempts_made":       attempts,
            "total_time_seconds":  round(elapsed, 2),
            "rate_limit_hits":     _RATE_LIMITER.rate_limit_hits,
        }).execute()
    except Exception as exc:
        log.debug("metrics insert failed: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# Top-level orchestrator
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class FetchAttempt:
    path:     str
    success:  bool
    quality:  str | None = None
    exhibit_url: str | None = None
    error:    str | None = None

    def to_dict(self) -> dict:
        return {"path": self.path, "success": self.success,
                "quality": self.quality, "exhibit_url": self.exhibit_url,
                "error_if_any": self.error}


def _try_8k_path(ticker: str, year: int, quarter: int,
                  path_label: str) -> tuple[dict | None, list[FetchAttempt]]:
    """Common subroutine used by PATH 1 and PATH 2. The caller restricts
    which filings to look at by passing a pre-filtered list."""
    attempts: list[FetchAttempt] = []
    cik = _resolve_cik(ticker)
    if not cik:
        attempts.append(FetchAttempt(path_label, False, error="CIK lookup failed"))
        return None, attempts
    filings = find_8k_transcript_filings(ticker, year, quarter)
    if path_label == "sec_8k_item701":
        # Item 7.01 is often filed AFTER the earnings 8-K. We restrict to
        # filings with "7.01" in the Items column, if known.
        filings = [f for f in filings
                   if f.items and "7.01" in (f.items or "")]
    if not filings:
        attempts.append(FetchAttempt(path_label, False,
                                       error="no 8-K filings in window"))
        return None, attempts

    log.info("%s: %s candidate 8-K filing(s) found", ticker, len(filings))
    earnings_date_guess = None
    for f in sorted(filings, key=lambda x: x.filed, reverse=True):
        exhibits = get_filing_exhibits(f.accession, cik)
        if not exhibits:
            continue
        # Try the top transcript-likely exhibits
        for ex in exhibits[:5]:
            if ex.score < 0.2:
                continue
            parsed = fetch_and_parse_exhibit(ex)
            if not parsed:
                attempts.append(FetchAttempt(path_label, False,
                                               exhibit_url=ex.url,
                                               error="exhibit parse failed"))
                continue
            text = parsed["text"]
            segments = structure_full_transcript(text)
            quality = validate_transcript_quality(text, segments)
            attempts.append(FetchAttempt(path_label, quality != "unavailable",
                                           quality=quality,
                                           exhibit_url=ex.url))
            if quality in ("full_call", "partial"):
                return {
                    "transcript_text":     text,
                    "structured_segments": [s.to_dict() for s in segments],
                    "quality_grade":       quality,
                    "source_url":          ex.url,
                    "source_path":         path_label,
                    "earnings_date":       f.filed.isoformat(),
                }, attempts
    return None, attempts


def _try_press_release_fallback(db, ticker: str, year: int,
                                   quarter: int) -> tuple[dict | None,
                                                            FetchAttempt]:
    """Fetch the actual EX-99.1 press release content from SEC -- much richer
    than the short description in sec_filings. This is still 'press_release_only'
    quality per spec, but the text body is several thousand words instead of
    one line."""
    cik = _resolve_cik(ticker)
    if cik:
        filings = find_8k_transcript_filings(ticker, year, quarter)
        # Restrict to filings whose Items list includes 2.02 (results of ops).
        for f in sorted(filings, key=lambda x: x.filed, reverse=True):
            if f.items and "2.02" not in f.items:
                continue
            exhibits = get_filing_exhibits(f.accession, cik)
            # Find the press-release exhibit -- description match or EX-99.1
            pr_ex = None
            for ex in exhibits:
                d = (ex.description or "").lower()
                t = (ex.type or "").lower()
                if "press release" in d or "earnings release" in d \
                        or t == "ex-99.1" or "99.1" in t:
                    pr_ex = ex
                    break
            if pr_ex is None and exhibits:
                pr_ex = exhibits[0]
            if pr_ex is None:
                continue
            parsed = fetch_and_parse_exhibit(pr_ex)
            if not parsed or not parsed.get("text"):
                continue
            return {
                "transcript_text":     parsed["text"],
                "structured_segments": [],
                "quality_grade":       "press_release_only",
                "source_url":          pr_ex.url,
                "source_path":         "press_release_fallback",
                "earnings_date":       f.filed.isoformat(),
            }, FetchAttempt("press_release_fallback", True,
                              quality="press_release_only",
                              exhibit_url=pr_ex.url)

    # Final-final fallback: short description from sec_filings
    try:
        rows = (db.table("sec_filings")
                .select("description,filing_url,filing_date,form_type")
                .eq("ticker", ticker).like("form_type", "8-K%")
                .order("filing_date", desc=True).limit(5).execute().data or [])
    except Exception as exc:
        return None, FetchAttempt("press_release_fallback", False,
                                    error=str(exc)[:80])
    if not rows:
        return None, FetchAttempt("press_release_fallback", False,
                                    error="no 8-K rows in sec_filings + no SEC exhibit")
    chosen = next((r for r in rows
                   if "2.02" in (r.get("description") or "")
                   or "result" in (r.get("description") or "").lower()), rows[0])
    desc = (chosen.get("description") or "").strip()
    if not desc:
        return None, FetchAttempt("press_release_fallback", False,
                                    error="empty description")
    return {
        "transcript_text":     desc,
        "structured_segments": [],
        "quality_grade":       "press_release_only",
        "source_url":          chosen.get("filing_url"),
        "source_path":         "press_release_fallback",
        "earnings_date":       (chosen.get("filing_date") or "")[:10],
    }, FetchAttempt("press_release_fallback", True,
                      quality="press_release_only")


def fetch_full_transcript(ticker: str, year: int, quarter: int,
                            db=None, use_cache: bool = True) -> dict:
    """Orchestrator. Tries 4 paths in order; returns the first success."""
    t0 = time.monotonic()
    ticker = ticker.upper()
    db_handle = db or _db()

    # Cache check
    if use_cache:
        cached = _read_disk_cache(ticker, year, quarter)
        if cached and cached.get("quality_grade") in ("full_call", "partial"):
            log.info("%s %dQ%d: disk cache hit (%s)",
                     ticker, year, quarter, cached["quality_grade"])
            return cached
        db_cached = _read_db_cache(db_handle, ticker, year, quarter)
        if db_cached and db_cached.get("quality_grade") in ("full_call", "partial"):
            log.info("%s %dQ%d: DB cache hit (%s)",
                     ticker, year, quarter, db_cached["quality_grade"])
            return db_cached

    attempts: list[FetchAttempt] = []

    # PATH 1: any 8-K with transcript exhibit
    result, a = _try_8k_path(ticker, year, quarter, "sec_8k_item202")
    attempts.extend(a)

    # PATH 2: Item 7.01 standalone (already a subset of PATH 1's filings;
    # restrict by items field if available)
    if result is None:
        result, a = _try_8k_path(ticker, year, quarter, "sec_8k_item701")
        attempts.extend(a)

    # PATH 3: IR page scrape -- skipped by default (fragile, anti-bot)
    # Implement on demand for specific tickers later

    # PATH 4: press release fallback (fetches actual EX-99.1 content)
    if result is None:
        result, a = _try_press_release_fallback(db_handle, ticker, year, quarter)
        attempts.append(a)
        if result is None:
            result = {
                "transcript_text":     "",
                "structured_segments": [],
                "quality_grade":       "unavailable",
                "source_url":          None,
                "source_path":         None,
                "earnings_date":       None,
            }

    payload = {
        "ticker":             ticker,
        "fiscal_year":        year,
        "fiscal_quarter":     quarter,
        **result,
    }

    elapsed = time.monotonic() - t0
    success = payload["quality_grade"] in ("full_call", "partial", "press_release_only")
    log.info("%s %dQ%d -> %s (%s, %.1fs)",
             ticker, year, quarter, payload["quality_grade"],
             payload.get("source_path"), elapsed)

    # Persist
    _write_disk_cache(ticker, year, quarter, payload)
    if success:
        _write_db_cache(db_handle, payload)
    _write_metrics(db_handle, ticker, year, quarter, success,
                     payload["quality_grade"], payload.get("source_path"),
                     [a.to_dict() for a in attempts], elapsed)

    return payload


# ══════════════════════════════════════════════════════════════════════════════
# CLI for debugging
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 4:
        print("usage: sec_transcript_fetcher.py TICKER YEAR QUARTER")
        sys.exit(2)
    ticker = sys.argv[1].upper()
    year = int(sys.argv[2])
    quarter = int(sys.argv[3])
    r = fetch_full_transcript(ticker, year, quarter)
    print(f"\nQUALITY:       {r['quality_grade']}")
    print(f"SOURCE PATH:   {r.get('source_path')}")
    print(f"SOURCE URL:    {r.get('source_url')}")
    print(f"EARNINGS DATE: {r.get('earnings_date')}")
    print(f"TEXT LENGTH:   {len(r['transcript_text']):,} chars / "
           f"{_word_count(r['transcript_text']):,} words")
    print(f"SEGMENTS:      {len(r['structured_segments'])}")
    if r["structured_segments"]:
        # Show first 5 + last 5 segments
        for s in r["structured_segments"][:5]:
            print(f"  [{s['segment_type']:<16}] {s['role']:<10} "
                  f"{s['name']:<35} {s['text'][:80]!r}")
        if len(r["structured_segments"]) > 10:
            print(f"  ... ({len(r['structured_segments']) - 10} more) ...")
        for s in r["structured_segments"][-5:]:
            print(f"  [{s['segment_type']:<16}] {s['role']:<10} "
                  f"{s['name']:<35} {s['text'][:80]!r}")


if __name__ == "__main__":
    main()
