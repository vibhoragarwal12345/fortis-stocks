"""
Audio Transcriber  --  Tier 4 of the transcript fallback hierarchy.

When the SEC 8-K exhibits (Tier 1/2) and the company IR RSS feed (Tier 3)
yield no usable transcript, this module transcribes the earnings-call webcast
audio directly.

Companies publicly webcast their earnings calls and post the audio replay on
their investor-relations pages for public consumption. We locate that audio,
download it once, and transcribe it locally with whisper (open-source, runs
offline -- see whisper_transcribe.py). whisper produces no speaker labels, so a
Groq pass attributes each segment to Operator / CEO / CFO / Analyst etc.
WITHOUT altering any words.

Etiquette (this module only touches company-owned IR pages, never third-party
sites):
  * one IR-page lookup per company per quarter -- results cached in
    data/audio_discovery_cache.json so we never re-probe
  * an identifying User-Agent
  * robots.txt is honoured: if a company disallows /investor*, we skip the
    audio path for that company and the chain falls through to press release
  * audio is downloaded once, cached on disk, never re-fetched; files older
    than 90 days are pruned (transcripts are kept long-term, audio is not)

Public entry point (called by sec_transcript_fetcher PATH 4):
    fetch_audio_transcript(ticker, year, quarter, db=None) -> dict | None

CLI:
    python pipeline/agents/audio_transcriber.py AAPL 2026 1
    python pipeline/agents/audio_transcriber.py --find AAPL 2026 1   # discovery only
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sys
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GROQ_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent          # pipeline/
AUDIO_CACHE_DIR = ROOT / "data" / "audio_cache"
DISCOVERY_CACHE = ROOT / "data" / "audio_discovery_cache.json"
IR_PAGE_CSV = ROOT / "data" / "ir_page_patterns.csv"

# TODO: replace with production-owned domain User-Agent before launching to real users.
IR_USER_AGENT = ("FortisStockIntelligence/1.0 "
                 "(+research bot; research@fortisagency.test)")
HTTP_TIMEOUT = 30
DISCOVERY_TTL_DAYS = 14          # re-probe a "no audio" result after this long
DISCOVERY_FOUND_TTL_DAYS = 365   # a known-good audio URL is reusable for a year
AUDIO_RETENTION_DAYS = 90        # prune cached audio files older than this
MAX_AUDIO_MB = 400               # sanity cap on a single download
GROQ_MODEL = "openai/gpt-oss-120b"   # matches the rest of the pipeline

AUDIO_EXT_RE = re.compile(r"\.(mp3|m4a|wav|mp4)(?:[?#]|$)", re.IGNORECASE)
_QA_TRIGGER_RE = re.compile(
    r"question[\- ]and[\- ]answer|q\s*&\s*a|first question|"
    r"take (?:our|the) first question", re.IGNORECASE)

_VALID_ROLES = {"Operator", "CEO", "CFO", "COO", "IR",
                "Analyst", "Other Officer", "Speaker"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Data model
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AudioSource:
    audio_url: str
    audio_format: str                 # mp3 | m4a | wav | mp4
    estimated_size_mb: float | None
    ir_page_url: str | None
    platform: str | None


# ══════════════════════════════════════════════════════════════════════════════
# HTTP + robots.txt
# ══════════════════════════════════════════════════════════════════════════════

_ROBOTS_CACHE: dict[str, urllib.robotparser.RobotFileParser] = {}


def _http_get(url: str, timeout: int = HTTP_TIMEOUT, stream: bool = False):
    try:
        return requests.get(
            url,
            headers={"User-Agent": IR_USER_AGENT, "Accept": "*/*"},
            timeout=timeout, stream=stream, allow_redirects=True)
    except requests.RequestException as exc:
        log.debug("GET %s failed: %s", url, exc)
        return None


def _robots_ok(url: str) -> bool:
    """True if our bot may fetch `url` per the host's robots.txt. A missing or
    unreadable robots.txt conventionally means unrestricted -> allowed."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
    except Exception:
        return True
    rp = _ROBOTS_CACHE.get(host)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            resp = requests.get(f"{host}/robots.txt",
                                headers={"User-Agent": IR_USER_AGENT},
                                timeout=15)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            elif resp.status_code in (401, 403):
                rp.disallow_all = True
            else:
                rp.allow_all = True
        except Exception:
            rp.allow_all = True
        _ROBOTS_CACHE[host] = rp
    try:
        return rp.can_fetch(IR_USER_AGENT, url)
    except Exception:
        return True


# ══════════════════════════════════════════════════════════════════════════════
# Discovery cache (one IR lookup per company per quarter)
# ══════════════════════════════════════════════════════════════════════════════

def _discovery_key(ticker: str, year: int, quarter: int) -> str:
    return f"{ticker}_{year}_Q{quarter}"


def _read_discovery_cache() -> dict:
    if DISCOVERY_CACHE.is_file():
        try:
            return json.loads(DISCOVERY_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write_discovery_cache(cache: dict) -> None:
    try:
        DISCOVERY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        DISCOVERY_CACHE.write_text(json.dumps(cache, indent=2, default=str),
                                   encoding="utf-8")
    except Exception as exc:
        log.debug("discovery cache write failed: %s", exc)


def _discovery_fresh(entry: dict) -> bool:
    try:
        checked = datetime.fromisoformat(entry["checked_at"])
    except Exception:
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - checked).total_seconds() / 86400
    ttl = (DISCOVERY_FOUND_TTL_DAYS if entry.get("outcome") == "found"
           else DISCOVERY_TTL_DAYS)
    return age_days < ttl


# ══════════════════════════════════════════════════════════════════════════════
# IR-page lookup + discovery
# ══════════════════════════════════════════════════════════════════════════════

def _load_ir_pages() -> dict[str, dict]:
    """Parse the hand-maintained ir_page_patterns.csv seed list."""
    out: dict[str, dict] = {}
    if not IR_PAGE_CSV.is_file():
        return out
    try:
        with IR_PAGE_CSV.open(encoding="utf-8") as fh:
            data_lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
        for row in csv.DictReader(data_lines):
            t = (row.get("ticker") or "").strip().upper()
            if t:
                out[t] = {
                    "ir_page_url": (row.get("ir_page_url") or "").strip(),
                    "platform": (row.get("platform") or "").strip() or None,
                    "company": (row.get("company") or "").strip(),
                }
    except Exception as exc:
        log.warning("failed to read %s: %s", IR_PAGE_CSV, exc)
    return out


def _discover_ir_page(ticker: str) -> str | None:
    """For a ticker not in the lookup CSV, find the company website via
    yfinance and probe the conventional investor-relations paths."""
    website = None
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        website = info.get("website") or info.get("irWebsite")
    except Exception as exc:
        log.debug("%s: yfinance website lookup failed: %s", ticker, exc)
    if not website:
        return None
    base = website.rstrip("/")
    netloc = urllib.parse.urlsplit(base).netloc
    domain = netloc[4:] if netloc.startswith("www.") else netloc
    candidates = [
        f"{base}/investor", f"{base}/investors",
        f"{base}/investor-relations", f"{base}/ir",
        f"https://investors.{domain}", f"https://investor.{domain}",
    ]
    for url in candidates:
        if not _robots_ok(url):
            continue
        r = _http_get(url)
        if r is not None and r.status_code == 200 and len(r.text) > 500:
            log.info("%s: discovered IR page %s", ticker, url)
            return url
    return None


def _extract_audio_links(page_url: str, html: str) -> list[str]:
    """Collect absolute .mp3/.m4a/.wav/.mp4 URLs referenced on an IR page."""
    found: list[str] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all(["a", "audio", "source", "link"]):
            href = tag.get("href") or tag.get("src") or ""
            if href:
                absurl = urllib.parse.urljoin(page_url, href)
                if AUDIO_EXT_RE.search(absurl):
                    found.append(absurl)
    except Exception as exc:
        log.debug("HTML parse of %s failed: %s", page_url, exc)
    # Catch URLs embedded in scripts/JSON that tag parsing misses (q4cdn etc).
    for m in re.finditer(r'https?://[^\s"\'<>]+?\.(?:mp3|m4a|wav)', html,
                         re.IGNORECASE):
        found.append(m.group(0))
    seen, out = set(), []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _validate_audio_url(url: str) -> tuple[bool, str, float | None]:
    """HEAD-check (with a ranged-GET fallback). Returns (ok, format, size_mb)."""
    resp = None
    try:
        resp = requests.head(url, headers={"User-Agent": IR_USER_AGENT},
                             timeout=HTTP_TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        resp = None
    if resp is None or resp.status_code >= 400:
        # Some CDNs reject HEAD -- retry with a one-byte ranged GET.
        try:
            resp = requests.get(
                url, headers={"User-Agent": IR_USER_AGENT, "Range": "bytes=0-0"},
                timeout=HTTP_TIMEOUT, allow_redirects=True, stream=True)
            resp.close()
        except requests.RequestException:
            return False, "", None
        if resp.status_code >= 400:
            return False, "", None

    ctype = (resp.headers.get("Content-Type") or "").lower()
    clen = resp.headers.get("Content-Length")
    size_mb = (round(int(clen) / 1_000_000, 1)
               if clen and clen.isdigit() else None)
    m = AUDIO_EXT_RE.search(url)
    fmt = m.group(1).lower() if m else ""
    if not fmt:
        if "mpeg" in ctype:
            fmt = "mp3"
        elif "m4a" in ctype or "mp4" in ctype:
            fmt = "m4a"
        elif "wav" in ctype:
            fmt = "wav"
    is_audio = bool(fmt) or "audio" in ctype or "video" in ctype
    return is_audio, (fmt or "mp3"), size_mb


def find_earnings_audio_url(ticker: str, year: int, quarter: int,
                            earnings_date: str | None = None
                            ) -> tuple[AudioSource | None, str]:
    """Locate a downloadable earnings-call audio file.

    Returns (AudioSource | None, outcome) where outcome is one of:
      found | no_ir_page | robots_disallow | no_audio | error
    """
    ticker = ticker.upper()
    entry = _load_ir_pages().get(ticker)
    if entry and entry.get("ir_page_url"):
        ir_url = entry["ir_page_url"]
        platform = entry.get("platform")
    else:
        ir_url = _discover_ir_page(ticker)
        platform = None
    if not ir_url:
        return None, "no_ir_page"

    if not _robots_ok(ir_url):
        log.info("%s: robots.txt disallows %s -- skipping audio path",
                 ticker, ir_url)
        return None, "robots_disallow"

    r = _http_get(ir_url)
    if r is None or r.status_code != 200:
        log.info("%s: IR page fetch failed (%s)", ticker, ir_url)
        return None, "error"

    links = _extract_audio_links(ir_url, r.text)
    if not links:
        log.info("%s: no direct audio links on %s", ticker, ir_url)
        return None, "no_audio"

    # Prefer links naming this quarter/year, and mp3 over other formats.
    q_labels = (f"q{quarter}", f"{quarter}q", f"q{quarter}-{year}",
                f"q{quarter}{str(year)[2:]}")

    def _rank(u: str) -> int:
        lo = u.lower()
        s = 0
        if any(ql in lo for ql in q_labels):
            s -= 4
        if str(year) in lo:
            s -= 2
        if lo.endswith(".mp3"):
            s -= 1
        return s

    for url in sorted(links, key=_rank):
        ok, fmt, size_mb = _validate_audio_url(url)
        if not ok:
            continue
        if size_mb is not None and size_mb > MAX_AUDIO_MB:
            log.info("%s: candidate audio too large (%.0f MB) -- skipping",
                     ticker, size_mb)
            continue
        log.info("%s %dQ%d: audio found (%s, %s MB)",
                 ticker, year, quarter, fmt, size_mb)
        return AudioSource(url, fmt, size_mb, ir_url, platform), "found"

    return None, "no_audio"


# ══════════════════════════════════════════════════════════════════════════════
# Download + transcribe
# ══════════════════════════════════════════════════════════════════════════════

def _download_audio(src: AudioSource, dest: Path) -> bool:
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    r = _http_get(src.audio_url, timeout=180, stream=True)
    if r is None or r.status_code >= 400:
        log.error("audio download HTTP error for %s", src.audio_url)
        return False
    tmp = dest.with_suffix(dest.suffix + ".part")
    total = 0
    try:
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_AUDIO_MB * 1_000_000:
                    raise RuntimeError(f"audio exceeded the {MAX_AUDIO_MB} MB cap")
                fh.write(chunk)
        tmp.replace(dest)
        return dest.is_file() and dest.stat().st_size > 0
    except Exception as exc:
        log.error("audio download failed for %s: %s", src.audio_url, exc)
        tmp.unlink(missing_ok=True)
        return False
    finally:
        r.close()


def transcribe_earnings_call(src: AudioSource, ticker: str, year: int,
                             quarter: int) -> dict | None:
    """Download the audio (once) and transcribe it. Returns the raw whisper
    output plus metadata, or None if no backend / download / transcription."""
    from agents.whisper_transcribe import available_backend, transcribe_audio

    if available_backend() is None:
        log.warning("no whisper backend installed -- skipping audio transcription "
                    "(run: bash pipeline/quant/install_whisper.sh)")
        return None

    ext = src.audio_format or "mp3"
    audio_file = AUDIO_CACHE_DIR / f"{ticker}_{year}_Q{quarter}.{ext}"
    if not (audio_file.is_file() and audio_file.stat().st_size > 0):
        log.info("%s %dQ%d: downloading earnings-call audio", ticker, year, quarter)
        if not _download_audio(src, audio_file):
            return None
    else:
        log.info("%s %dQ%d: using cached audio file", ticker, year, quarter)

    size_mb = round(audio_file.stat().st_size / 1_000_000, 1)
    try:
        w = transcribe_audio(audio_file)
    except Exception as exc:
        log.error("%s %dQ%d: transcription failed: %s",
                  ticker, year, quarter, exc)
        return None

    method = ("whisper_cpp_medium_en" if w["backend"] == "whisper.cpp"
              else "whisper_medium_en")
    return {
        "raw_transcript": w["text"],
        "whisper_segments": w["segments"],
        "confidence": w["confidence"],
        "transcription_method": method,
        "transcription_time_seconds": w["duration_seconds"],
        "audio_size_mb": size_mb,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Speaker attribution (Groq) -- adds labels, never changes words
# ══════════════════════════════════════════════════════════════════════════════

_STRUCTURE_SYSTEM = (
    "You analyze earnings-call transcripts. You are given a call as a numbered "
    "list of short speech segments produced by automatic speech recognition "
    "(no speaker labels). Identify who is speaking.\n\n"
    "An earnings call has: an Operator who opens the call and runs the Q&A; "
    "company executives (CEO, CFO, sometimes COO or an Investor Relations "
    "officer) who give prepared remarks and answer questions; and sell-side "
    "Analysts who ask questions during Q&A.\n\n"
    "Return ONLY the segment indices where the speaker CHANGES, as a JSON "
    "object: {\"transitions\": [{\"segment_index\": int, \"speaker\": str, "
    "\"role\": str}]}. The first transition MUST be segment_index 0. role must "
    "be exactly one of: Operator, CEO, CFO, COO, IR, Analyst, Other Officer, "
    "Speaker. Use the person's name as \"speaker\" when it is stated in the "
    "transcript; otherwise use their role.\n\n"
    "Do NOT change, summarize, paraphrase, or invent any words. You only label "
    "who is speaking."
)


def _normalize_role(role: str | None) -> str:
    r = (role or "").strip()
    for v in _VALID_ROLES:
        if r.lower() == v.lower():
            return v
    rl = r.lower()
    if "operator" in rl or "coordinator" in rl or "moderator" in rl:
        return "Operator"
    if "chief executive" in rl:
        return "CEO"
    if "chief financial" in rl:
        return "CFO"
    if "chief operating" in rl:
        return "COO"
    if "analyst" in rl or "research" in rl:
        return "Analyst"
    if "investor relations" in rl:
        return "IR"
    return "Other Officer" if rl else "Speaker"


def _groq_speaker_transitions(seg_texts: list[str]) -> list[dict]:
    """Ask Groq for speaker-change points. Returns a sorted, validated list."""
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY not set -- speaker attribution will degrade")
        return []
    max_segs = 1600          # ~a full 60-min call; keeps well within context
    if len(seg_texts) > max_segs:
        log.info("transcript has %d ASR segments; structuring the first %d",
                 len(seg_texts), max_segs)
        seg_texts = seg_texts[:max_segs]
    numbered = "\n".join(f"[{i}] {t}" for i, t in enumerate(seg_texts))
    try:
        from groq import Groq
        resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": _STRUCTURE_SYSTEM},
                      {"role": "user",
                       "content": f"Numbered ASR segments:\n\n{numbered}"}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        payload = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:
        log.warning("Groq speaker structuring failed: %s", exc)
        return []

    cleaned: list[dict] = []
    for t in payload.get("transitions") or []:
        try:
            idx = int(t.get("segment_index"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(seg_texts):
            cleaned.append({
                "segment_index": idx,
                "speaker": (t.get("speaker") or "Speaker").strip()[:80] or "Speaker",
                "role": _normalize_role(t.get("role")),
            })
    cleaned.sort(key=lambda x: x["segment_index"])
    return cleaned


def _assign_segment_types(turns: list[dict]) -> list[dict]:
    """Tag each speaker turn as operator / prepared_remarks / qa_question /
    qa_answer, mirroring sec_transcript_fetcher.structure_full_transcript."""
    qa_start = None
    for i, t in enumerate(turns):
        if t["role"] == "Operator" and _QA_TRIGGER_RE.search(t["text"]):
            qa_start = i + 1
            break
    if qa_start is None:
        for i, t in enumerate(turns):
            if t["role"] == "Analyst":
                qa_start = i
                break

    out: list[dict] = []
    for i, t in enumerate(turns):
        in_qa = qa_start is not None and i >= qa_start
        if t["role"] == "Operator":
            seg_type = "operator"
        elif in_qa:
            seg_type = "qa_question" if t["role"] == "Analyst" else "qa_answer"
        else:
            seg_type = "prepared_remarks"
        out.append({"role": t["role"], "name": t["speaker"],
                    "text": t["text"], "segment_type": seg_type})
    return out


def _fallback_structure(raw_text: str) -> list[dict]:
    """Used when Groq is unavailable. Produces a single un-attributed segment;
    the transcript will then grade 'partial' rather than 'full_call'."""
    log.warning("speaker attribution unavailable -- emitting unstructured segment")
    return [{"role": "Speaker", "name": "Speaker",
             "text": raw_text, "segment_type": "prepared_remarks"}]


def enhance_transcript_structure(raw_text: str,
                                 whisper_segments: list[dict]) -> list[dict]:
    """Turn raw whisper output into [{role, name, text, segment_type}] -- the
    same structured shape sec_transcript_fetcher produces for SEC transcripts.

    Words are never altered: segment text is always a verbatim join of the
    whisper segments; Groq only supplies speaker-change indices and labels."""
    seg_texts = [(s.get("text") or "").strip() for s in whisper_segments]
    seg_texts = [t for t in seg_texts if t]
    if not seg_texts:
        return _fallback_structure(raw_text)

    transitions = _groq_speaker_transitions(seg_texts)
    if not transitions:
        return _fallback_structure(raw_text)

    if transitions[0]["segment_index"] != 0:
        transitions.insert(0, {"segment_index": 0, "speaker": "Speaker",
                               "role": "Speaker"})

    turns: list[dict] = []
    for ti, tr in enumerate(transitions):
        start = tr["segment_index"]
        end = (transitions[ti + 1]["segment_index"]
               if ti + 1 < len(transitions) else len(seg_texts))
        body = " ".join(seg_texts[start:end]).strip()
        if body:
            turns.append({"speaker": tr["speaker"], "role": tr["role"],
                          "text": body})
    return _assign_segment_types(turns) if turns else _fallback_structure(raw_text)


# ══════════════════════════════════════════════════════════════════════════════
# audio_transcripts table cache
# ══════════════════════════════════════════════════════════════════════════════

def _db():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _read_audio_db(db, ticker: str, year: int, quarter: int) -> dict | None:
    try:
        rows = (db.table("audio_transcripts").select("*")
                .eq("ticker", ticker).eq("fiscal_year", year)
                .eq("fiscal_quarter", quarter).limit(1).execute().data or [])
    except Exception as exc:
        log.debug("audio_transcripts read failed: %s", exc)
        return None
    return rows[0] if rows else None


def _write_audio_db(db, row: dict) -> None:
    try:
        db.table("audio_transcripts").upsert(
            row, on_conflict="ticker,fiscal_year,fiscal_quarter").execute()
    except Exception as exc:
        log.warning("audio_transcripts upsert failed for %s: %s",
                    row.get("ticker"), exc)


def _row_to_result(row: dict) -> dict:
    """Convert a cached audio_transcripts row into the standard transcript
    dict the orchestrator expects."""
    segments = row.get("structured_segments") or []
    # Re-grade from the cached structure so quality stays consistent.
    quality = "full_call_prepared_only"   # safe default if grading fails
    try:
        from agents.sec_transcript_fetcher import (Segment,
                                                   validate_transcript_quality)
        seg_objs = [Segment(role=s.get("role", "Speaker"),
                            name=s.get("name", "Speaker"),
                            text=s.get("text", ""),
                            segment_type=s.get("segment_type", "prepared_remarks"))
                    for s in segments]
        quality = validate_transcript_quality(row.get("raw_transcript") or "",
                                              seg_objs)
    except Exception:
        pass
    return {
        "transcript_text": row.get("raw_transcript") or "",
        "structured_segments": segments,
        "quality_grade": quality,
        "source_url": row.get("audio_url"),
        "source_path": "audio_transcription",
        "earnings_date": row.get("earnings_date"),
    }


def _cleanup_old_audio() -> None:
    """Delete cached audio files older than the retention window. Transcripts
    are kept permanently in the DB; the bulky audio is not."""
    if not AUDIO_CACHE_DIR.is_dir():
        return
    cutoff = time.time() - AUDIO_RETENTION_DAYS * 86400
    removed = 0
    for f in AUDIO_CACHE_DIR.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            continue
    if removed:
        log.info("pruned %d cached audio file(s) older than %d days",
                 removed, AUDIO_RETENTION_DAYS)


# ══════════════════════════════════════════════════════════════════════════════
# PATH 4 entry point
# ══════════════════════════════════════════════════════════════════════════════

def fetch_audio_transcript(ticker: str, year: int, quarter: int,
                           db=None, earnings_date: str | None = None
                           ) -> dict | None:
    """Tier-4 fallback: transcribe the earnings-call webcast audio.

    Returns the standard transcript dict (source_path = 'audio_transcription')
    or None if no audio is available / no whisper backend is installed."""
    # Fast no-op when no whisper backend is installed -- skip all network I/O.
    from agents.whisper_transcribe import available_backend
    if available_backend() is None:
        log.info("%s: audio path skipped -- no whisper backend "
                 "(run: bash pipeline/quant/install_whisper.sh)", ticker.upper())
        return None

    ticker = ticker.upper()
    db_handle = db
    if db_handle is None:
        try:
            db_handle = _db()
        except Exception:
            db_handle = None

    # 1. audio_transcripts cache -- a transcribed quarter is never re-done.
    if db_handle is not None:
        cached = _read_audio_db(db_handle, ticker, year, quarter)
        if cached and cached.get("raw_transcript"):
            log.info("%s %dQ%d: audio_transcripts cache hit", ticker, year, quarter)
            return _row_to_result(cached)

    # 2. discovery cache -- one IR-page lookup per company per quarter.
    dcache = _read_discovery_cache()
    key = _discovery_key(ticker, year, quarter)
    entry = dcache.get(key)
    src: AudioSource | None = None
    if entry and _discovery_fresh(entry):
        if entry.get("outcome") != "found":
            log.info("%s %dQ%d: discovery cache = '%s' -- skipping audio path",
                     ticker, year, quarter, entry.get("outcome"))
            return None
        src = AudioSource(entry["audio_url"], entry.get("audio_format") or "mp3",
                          entry.get("estimated_size_mb"),
                          entry.get("ir_page_url"), entry.get("platform"))
    else:
        # 3. discover the audio URL and remember the outcome
        src, outcome = find_earnings_audio_url(ticker, year, quarter, earnings_date)
        dcache[key] = {
            "outcome": outcome,
            "audio_url": src.audio_url if src else None,
            "audio_format": src.audio_format if src else None,
            "estimated_size_mb": src.estimated_size_mb if src else None,
            "ir_page_url": src.ir_page_url if src else None,
            "platform": src.platform if src else None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_discovery_cache(dcache)
        if src is None:
            return None

    # 4. download + transcribe (the slow step: 5-10 min on a modern CPU)
    tr = transcribe_earnings_call(src, ticker, year, quarter)
    if tr is None:
        return None

    # 5. speaker attribution
    structured = enhance_transcript_structure(tr["raw_transcript"],
                                              tr["whisper_segments"])

    # 6. grade completeness with the shared SEC grader
    quality = "full_call_prepared_only"   # safe default if grading fails
    try:
        from agents.sec_transcript_fetcher import (Segment,
                                                   validate_transcript_quality)
        seg_objs = [Segment(role=s["role"], name=s["name"], text=s["text"],
                            segment_type=s["segment_type"])
                    for s in structured]
        quality = validate_transcript_quality(tr["raw_transcript"], seg_objs)
    except Exception as exc:
        log.warning("quality grading failed, defaulting to 'partial': %s", exc)

    # 7. persist to audio_transcripts
    if db_handle is not None:
        now = datetime.now(timezone.utc).isoformat()
        _write_audio_db(db_handle, {
            "ticker": ticker, "fiscal_year": year, "fiscal_quarter": quarter,
            "earnings_date": earnings_date,
            "audio_url": src.audio_url,
            "audio_size_mb": tr["audio_size_mb"],
            "transcription_method": tr["transcription_method"],
            "raw_transcript": tr["raw_transcript"],
            "structured_segments": structured,
            "transcription_quality_estimate": tr["confidence"],
            "transcription_time_seconds": tr["transcription_time_seconds"],
            "transcribed_at": now,
        })

    _cleanup_old_audio()

    log.info("%s %dQ%d: audio transcription complete -> %s "
             "(%d segments, whisper confidence=%s)",
             ticker, year, quarter, quality, len(structured), tr["confidence"])
    return {
        "transcript_text": tr["raw_transcript"],
        "structured_segments": structured,
        "quality_grade": quality,
        "source_url": src.audio_url,
        "source_path": "audio_transcription",
        "earnings_date": earnings_date,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def _main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--find":
        if len(args) < 4:
            print("usage: audio_transcriber.py --find TICKER YEAR QUARTER")
            sys.exit(2)
        src, outcome = find_earnings_audio_url(args[1], int(args[2]), int(args[3]))
        print(f"outcome: {outcome}")
        if src:
            print(f"audio_url: {src.audio_url}")
            print(f"format:    {src.audio_format}")
            print(f"size_mb:   {src.estimated_size_mb}")
            print(f"ir_page:   {src.ir_page_url}")
        return

    if len(args) < 3:
        print("usage: audio_transcriber.py TICKER YEAR QUARTER")
        print("       audio_transcriber.py --find TICKER YEAR QUARTER")
        sys.exit(2)
    r = fetch_audio_transcript(args[0], int(args[1]), int(args[2]))
    if r is None:
        print("\nNo audio transcript produced (see log above).")
        return
    print(f"\nQUALITY:      {r['quality_grade']}")
    print(f"SOURCE URL:   {r['source_url']}")
    print(f"SEGMENTS:     {len(r['structured_segments'])}")
    print(f"TEXT LENGTH:  {len(r['transcript_text']):,} chars")
    for s in r["structured_segments"][:6]:
        print(f"  [{s['segment_type']:<16}] {s['role']:<10} {s['name']:<28} "
              f"{s['text'][:70]!r}")


if __name__ == "__main__":
    _main()
