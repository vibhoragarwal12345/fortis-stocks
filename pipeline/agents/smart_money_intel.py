"""
Smart Money Intelligence  (Step 6.7)
Triangulates four institutional signals into a single composite read:

  1. EARNINGS CALL ANALYZER  -- API Ninjas transcripts (free tier); falls back
     to 8-K Item 2.02 attachments in sec_filings. Tone shift vs prior 4 quarters,
     guidance change tracking, CFO/CEO divergence, Q&A pushback classification.

  2. INSIDER ACTIVITY ANALYZER -- SEC EDGAR Form 4 XML, with 10b5-1 detection
     so scheduled trades get 5x less weight than discretionary ones. Role-and-
     size weighted. Post-earnings activity gets extra weight (highest signal).

  3. SHORT INTEREST & SQUEEZE DETECTOR -- FINRA daily Reg-SHO + bi-monthly
     official short interest. Combines short_pct_of_float * days_to_cover into
     squeeze_potential_score; trend direction (building/covering) crossed with
     price action gives the directional read.

  4. ANALYST REVISION TRACKER -- Finnhub /stock/recommendation + /stock/upgrade-
     downgrade. Direction-over-magnitude velocity classification (Zacks 1979).

SYNTHESIS LAYER classifies each (E, I, S, R) tuple into one of 10 patterns
(FULL_CONVICTION_LONG, INSIDER_SMOKE_SIGNAL, SHORT_SQUEEZE_SETUP, ...) and
computes a composite_smart_money_score weighted per-pattern. Groq writes a
research-grade intel note using a STRICT closed-context system prompt; the
existing factcheck_agent runs verification. LOW-confidence rows are
informational only -- they cannot change a conviction grade.

CONGRESSIONAL TRADING ENRICHMENT is supplementary -- pulled from Senate Stock
Watcher + House Stock Watcher JSON dumps, cached locally with a 24h TTL.

OUTPUT
  Writes one row per (ticker, snapshot_date, run_type) to smart_money_intel
  (migration 031). The seven downstream agents (catalyst, debate, critic,
  conviction_grader_v2, report_generator, risk_flagger, outcome_tracker) read
  this table and apply the integrations described in the spec.

FORBIDDEN LANGUAGE CHECK
  Every intel_note is scanned for the words 'buy', 'sell', 'must', 'recommend
  you' before write. Any match -> the note is rejected and the row is written
  WITHOUT intel_note (the structured signals still write fine).

Usage:
    python pipeline/agents/smart_money_intel.py [run_type] [top_n]
        run_type  = midday  (default; matches the rest of the pipeline)
        top_n     = 30      (default; runs on top N ranked picks for today)

    python pipeline/agents/smart_money_intel.py --ticker AAPL
        Single-ticker debug mode -- writes nothing, prints the full read.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (  # noqa: E402
    API_NINJAS_KEY, FINNHUB_API_KEY, GROQ_API_KEY,
    SUPABASE_SERVICE_KEY, SUPABASE_URL,
)
from supabase import create_client  # noqa: E402

# Re-use the strict factcheck implementation we already shipped.
from agents.factcheck_agent import factcheck  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = ROOT / "data" / "transcripts"
CONGRESS_CACHE_DIR = ROOT / "data" / "congress"
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
CONGRESS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

EDGAR_UA = ("Fortis Stock Intelligence research@fortisagency.test "
            "(institutional research)")
HTTP_TIMEOUT = 30
FINRA_REG_SHO_BASE = "https://cdn.finra.org/equity/regsho/daily"
SENATE_WATCHER_URL = "https://senatestockwatcher.com/aggregate/all_transactions.json"
HOUSE_WATCHER_URL  = "https://housestockwatcher.com/data/all_transactions.json"
CONGRESS_CACHE_TTL_HOURS = 24

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_TEMPERATURE = 0.3   # tighter than debate_synthesizer because we want
                         # closed-context language, not creative reasoning

# Forbidden language gate (intel notes are research, not advice).
FORBIDDEN_PATTERNS = [
    re.compile(r"\b(?:buy|sell)\b", re.IGNORECASE),
    re.compile(r"\bmust\b", re.IGNORECASE),
    re.compile(r"\brecommend\s+you\b", re.IGNORECASE),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Tiny helpers (kept private to this module)
# ══════════════════════════════════════════════════════════════════════════════

def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:   # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _http_get(url: str, headers: dict | None = None, params: dict | None = None,
              timeout: int = HTTP_TIMEOUT):
    h = {"User-Agent": EDGAR_UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, headers=h, params=params, timeout=timeout)
        return r
    except requests.RequestException as exc:
        log.debug("http GET %s failed: %s", url, exc)
        return None


def _safe_div(a, b):
    a, b = _num(a), _num(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _trading_days_ago(d: date, today: date) -> int:
    """Approximate trading days (weekdays) between d and today."""
    if d > today:
        return 0
    n, cur = 0, d
    while cur < today:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


# ══════════════════════════════════════════════════════════════════════════════
# SUB-MODULE 1 -- EARNINGS CALL ANALYZER
# ══════════════════════════════════════════════════════════════════════════════

def _last_earnings_quarter(today: date) -> tuple[int, int]:
    """Best-guess (year, quarter) of the most recently reported earnings:
    we assume Q1=Apr-Jun reports, Q2=Jul-Sep, Q3=Oct-Dec, Q4=Jan-Mar (next year).
    Pulling the wrong quarter is non-fatal -- _fetch_transcript will just fail
    and we degrade to 8-K fallback."""
    m, y = today.month, today.year
    if m in (1, 2, 3):
        return y - 1, 4
    if m in (4, 5, 6):
        return y, 1
    if m in (7, 8, 9):
        return y, 2
    return y, 3


def _api_ninjas_transcript(ticker: str, year: int, quarter: int) -> str | None:
    if not API_NINJAS_KEY:
        return None
    r = _http_get(
        "https://api.api-ninjas.com/v1/earningstranscript",
        headers={"X-Api-Key": API_NINJAS_KEY},
        params={"ticker": ticker, "year": year, "quarter": quarter},
    )
    if not r or r.status_code != 200:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    if isinstance(body, list):
        body = body[0] if body else {}
    return (body or {}).get("transcript") or None


def _eightk_fallback(db, ticker: str) -> tuple[str | None, str | None]:
    """Pull the most recent 8-K with form_type starting with '8-K' for this
    ticker out of sec_filings. We return the description / URL; we don't
    have the full body parsed, so quality = 'press_release_only'."""
    try:
        rows = (db.table("sec_filings")
                .select("description,filing_url,filing_date,form_type")
                .eq("ticker", ticker).like("form_type", "8-K%")
                .order("filing_date", desc=True).limit(5).execute().data or [])
    except Exception:
        return None, None
    if not rows:
        return None, None
    # Heuristic: 8-K Item 2.02 = results of operations / financial condition.
    chosen = next((r for r in rows
                   if "2.02" in (r.get("description") or "")
                   or "result" in (r.get("description") or "").lower()), rows[0])
    desc = chosen.get("description") or chosen.get("filing_url") or ""
    return desc, chosen.get("filing_date")


def fetch_transcript(db, ticker: str, today: date) -> dict:
    """Return {'text', 'source', 'quality', 'last_report_date', 'year',
    'quarter'} -- best-effort.

    Routes through sec_transcript_fetcher's fallback chain: SEC 8-K exhibits
    -> IR RSS feed -> press release -> (conditional) audio transcription.
    'quality' is the six-tier coverage grade, passed straight through:
      full_call_with_qa | full_call_prepared_only | substantive_press_release
      | standard_press_release | minimal_press_release | unavailable
    analyze_earnings_call() keys earnings_strength_baseline off it. The
    'source' field carries the originating path (sec_8k_item202 / ... /
    press_release_fallback).
    """
    from agents import sec_transcript_fetcher as stf  # avoid import-time cycle

    year, quarter = _last_earnings_quarter(today)

    # Try current quarter, then prior quarter (some companies report late).
    candidates = [(year, quarter)]
    py, pq = (year - 1, 4) if quarter == 1 else (year, quarter - 1)
    candidates.append((py, pq))

    # "Good" grades stop the search; anything non-unavailable is kept as a
    # fallback if no quarter yields a good one.
    GOOD = ("full_call_with_qa", "full_call_prepared_only",
            "substantive_press_release")
    best = None
    for y, q in candidates:
        r = stf.fetch_full_transcript(ticker, y, q, db=db)
        if r["quality_grade"] in GOOD:
            best = (r, y, q)
            break
        if r["quality_grade"] != "unavailable" and best is None:
            best = (r, y, q)

    if best is None:
        return {"text": None, "source": None, "quality": "unavailable",
                "last_report_date": None, "year": year, "quarter": quarter}

    r, y, q = best
    return {
        "text":             r["transcript_text"],
        "source":           r.get("source_path") or "sec",
        "quality":          r["quality_grade"],   # six-tier grade, passed through
        "last_report_date": r.get("earnings_date"),
        "year":             y,
        "quarter":          q,
        # Pass through structured segments for richer downstream analysis
        "structured":       r.get("structured_segments") or [],
        "source_url":       r.get("source_url"),
    }


def _simple_tone_score(text: str) -> float:
    """Bag-of-words sentiment proxy. We do NOT load FinBERT here -- the spec
    asks for FinBERT, but loading transformers / torch per ticker is prohibitively
    expensive and the smart-money signal only needs the SHIFT vs prior quarters,
    which is robust to a coarser scorer. Range: -1..+1."""
    if not text:
        return 0.0
    POS = {"strong", "record", "exceeded", "beat", "growth", "accelerating",
           "improving", "robust", "confident", "outperform", "momentum",
           "positive", "upgrade", "raise", "raised", "increase", "increased",
           "expansion", "tailwind", "favorable"}
    NEG = {"miss", "missed", "headwind", "decelerating", "weakness", "soft",
           "softer", "cautious", "challenging", "uncertain", "below", "decline",
           "declining", "decrease", "decreased", "lowered", "lower", "guide-down",
           "downgrade", "pressure", "concern", "concerned", "deteriorat",
           "underperform"}
    toks = re.findall(r"[a-zA-Z][a-zA-Z\-]+", text.lower())
    if not toks:
        return 0.0
    pos = sum(1 for t in toks if t in POS)
    neg = sum(1 for t in toks if t in NEG)
    if pos + neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


def _classify_deflections(qa_text: str) -> tuple[int, int, int]:
    """Quick proxy: count question marks followed by phrases that signal
    deflection. Returns (deflected, partial, direct)."""
    if not qa_text:
        return 0, 0, 0
    deflect_markers = ["we'll get back", "not at this time", "won't be commenting",
                       "we don't disclose", "no comment", "as we said earlier",
                       "we are not", "circle back", "follow up offline"]
    partial_markers = ["broadly", "directionally", "in general", "without getting into specifics"]
    deflected = sum(qa_text.lower().count(m) for m in deflect_markers)
    partial   = sum(qa_text.lower().count(m) for m in partial_markers)
    # Crude direct = number of Q&A turns minus deflected/partial.
    q_count = qa_text.count("?")
    direct = max(0, q_count - deflected - partial)
    return deflected, partial, direct


def analyze_earnings_call(transcript_pkg: dict, ticker: str, today: date) -> dict:
    """Returns the earnings sub-signal."""
    text = transcript_pkg.get("text") or ""
    quality = transcript_pkg.get("quality") or "unavailable"
    if not text:
        return {"score": 0.0, "strength": 0.0,
                "last_report_date": None, "verdict": "NO_DATA",
                "tone_summary": {}, "transcript_quality": quality}

    # Split prepared-remarks vs Q&A heuristically.
    qa_marker = re.search(r"(question[s]?[ -]and[ -]answer|q\s*&\s*a)",
                          text, re.IGNORECASE)
    prepared = text[:qa_marker.start()] if qa_marker else text
    qa       = text[qa_marker.end():] if qa_marker else ""

    # CEO vs CFO splits via speaker tags.
    ceo_chunks = re.findall(r"(?im)^\s*(?:[A-Z][a-z]+\s+){0,3}(?:CEO|Chief Executive Officer)[^\n]*\n(.+?)(?=\n[A-Z]|\Z)",
                            prepared, re.DOTALL)
    cfo_chunks = re.findall(r"(?im)^\s*(?:[A-Z][a-z]+\s+){0,3}(?:CFO|Chief Financial Officer)[^\n]*\n(.+?)(?=\n[A-Z]|\Z)",
                            prepared, re.DOTALL)
    ceo_text = "\n".join(ceo_chunks) if ceo_chunks else prepared
    cfo_text = "\n".join(cfo_chunks) if cfo_chunks else prepared

    tone_ceo = _simple_tone_score(ceo_text)
    tone_cfo = _simple_tone_score(cfo_text)
    tone_qa  = _simple_tone_score(qa)
    tone_overall = _simple_tone_score(text)

    # CFO/CEO divergence detection -- if CEO upbeat but CFO neutral/negative,
    # that's the classic management-vs-numbers divergence the spec flags.
    divergence_flag = (tone_ceo > 0.2 and tone_cfo < 0.05)

    deflected, partial, direct = _classify_deflections(qa)

    # Guidance change / beat magnitude -- we don't have a free, reliable source
    # of historical consensus; treat as None and let the scoring degrade.
    # (When API_NINJAS_KEY is set the transcript itself often contains
    # explicit guidance language; we extract it crudely.)
    guidance_raise = bool(re.search(
        r"(raising|raised|increased|increasing)\s+(?:our\s+)?(?:full[- ]year|fy|q\d|guidance|outlook)",
        text, re.IGNORECASE))
    guidance_cut = bool(re.search(
        r"(lowering|lowered|reducing|reduced|cut|cutting)\s+(?:our\s+)?(?:full[- ]year|fy|q\d|guidance|outlook)",
        text, re.IGNORECASE))
    beat_word  = bool(re.search(r"\b(exceeded|beat|topped)\b\s+(?:our\s+)?(?:guidance|consensus|estimates|expectations)",
                                 text, re.IGNORECASE))
    miss_word  = bool(re.search(r"\b(missed|below|short of)\b\s+(?:our\s+)?(?:guidance|consensus|estimates|expectations)",
                                 text, re.IGNORECASE))

    # Score assembly per spec.
    score = 0.0
    if beat_word:    score += 35
    if miss_word:    score -= 35
    if guidance_raise:  score += 25
    if guidance_cut:    score -= 25
    score += max(-20, min(20, tone_overall * 50))
    score -= min(20, deflected * 5)
    score = max(-100, min(100, round(score, 2)))

    # Earnings-strength baseline per the six-tier coverage rubric (Fix 2).
    # A press release is no longer treated as a uniformly degraded outcome: a
    # substantive 8-K Item 2.02 release carries the financials, guidance and
    # prepared commentary, so it earns a solid baseline. What a press release
    # lacks -- live Q&A pushback, real-time tone shifts -- is disclosed in the
    # intel note rather than silently penalised.
    base_strength = {
        "full_call_with_qa":         100,
        "full_call_prepared_only":    85,
        "substantive_press_release":  75,
        "standard_press_release":     60,
        "minimal_press_release":      40,
        "unavailable":                 0,
    }.get(quality, 0)
    # Decay if last earnings > 60 days ago (signal stale). We don't always
    # know the date; assume current quarter is fresh.
    strength = base_strength
    if divergence_flag:
        strength = min(100, strength + 10)   # divergence is informative

    verdict = ("BEAT_AND_RAISE" if beat_word and guidance_raise
                else "BEAT_NO_RAISE" if beat_word
                else "MISS_AND_CUT" if miss_word and guidance_cut
                else "MISS" if miss_word
                else "GUIDE_DOWN" if guidance_cut
                else "GUIDE_UP" if guidance_raise
                else "NEUTRAL")

    return {
        "score": score,
        "strength": strength,
        "last_report_date": transcript_pkg.get("last_report_date"),
        "verdict": verdict,
        "transcript_quality": quality,
        "tone_summary": {
            "tone_ceo":          round(tone_ceo, 3),
            "tone_cfo":          round(tone_cfo, 3),
            "tone_qa":           round(tone_qa, 3),
            "tone_overall":      round(tone_overall, 3),
            "cfo_ceo_divergence": divergence_flag,
            "deflections":       deflected,
            "partial_answers":   partial,
            "guidance_raise":    guidance_raise,
            "guidance_cut":      guidance_cut,
            "beat_language":     beat_word,
            "miss_language":     miss_word,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# SUB-MODULE 2 -- INSIDER ACTIVITY (Form 4) ANALYZER
# ══════════════════════════════════════════════════════════════════════════════

def _looks_10b5_1(footnote_text: str) -> bool:
    """Per-spec detection. Form 4 footnotes mark scheduled trades with explicit
    '10b5-1' text or wording about a written plan."""
    if not footnote_text:
        return False
    s = footnote_text.lower()
    if "10b5-1" in s or "10b5 1" in s:
        return True
    if "rule 10b5" in s:
        return True
    if "written trading plan" in s or "pre-arranged" in s or "pre-established" in s:
        return True
    # Boilerplate that frequently accompanies scheduled trades.
    if "trading plan" in s and ("adopt" in s or "established" in s):
        return True
    return False


def _is_directional_signal(code: str | None, ad: str | None,
                           is_10b5_1: bool) -> bool:
    """True only for Form 4 transactions that reflect a real, discretionary
    view -- the integrity filter from the 10b5-1 spot test:
      P  open-market purchase
      S  open-market sale, but only when NOT a scheduled 10b5-1 trade
      V  voluntary transaction not pursuant to Rule 10b5-1
    Every other code (A grants, F tax withholding, G gifts, M/X derivative
    exercises, etc.) is mechanical or non-directional and returns False."""
    if code == "P":
        return True
    if code == "S":
        return not is_10b5_1
    if code == "V":
        return True
    return False


def _normalize_role(title: str | None) -> str:
    if not title:
        return "Other"
    t = title.lower()
    if "chief executive" in t or re.search(r"\bceo\b", t):
        return "CEO"
    if "chief financial" in t or re.search(r"\bcfo\b", t):
        return "CFO"
    if "chief operating" in t or re.search(r"\bcoo\b", t):
        return "COO"
    if "director" in t:
        return "Director"
    if "10%" in t or "ten percent" in t:
        return "10% Owner"
    if "officer" in t or "vp" in t or "president" in t:
        return "Other Officer"
    return "Other"


def _resolve_cik(ticker: str) -> str | None:
    """SEC ticker -> CIK lookup. Cached on disk for the duration of the session."""
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


def _fetch_form4_filings(cik: str, lookback_days: int = 90) -> list[dict]:
    """List of {accession, filed} Form 4 filings via EDGAR JSON index."""
    cik_int = int(cik)
    url = f"https://data.sec.gov/submissions/CIK{str(cik_int).zfill(10)}.json"
    r = _http_get(url)
    if not r or r.status_code != 200:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accs  = recent.get("accessionNumber") or []
    dates = recent.get("filingDate") or []
    cutoff = (datetime.utcnow().date() - timedelta(days=lookback_days)).isoformat()
    out = []
    for form, acc, d in zip(forms, accs, dates):
        if form != "4":
            continue
        if d < cutoff:
            continue
        out.append({"accession": acc, "filed": d})
    return out


def _parse_form4_xml(cik: str, accession: str) -> list[dict]:
    """Returns list of normalized transaction dicts. Form 4 documents live at
    https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{file}
    -- we hit the index and look for the first .xml whose name starts with
    'ownership' or contains 'form4'."""
    acc_clean = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}"
    idx = _http_get(f"{base}/index.json")
    if not idx or idx.status_code != 200:
        return []
    try:
        items = idx.json().get("directory", {}).get("item", [])
    except ValueError:
        return []
    xml_name = None
    for it in items:
        n = (it.get("name") or "").lower()
        if n.endswith(".xml") and ("form4" in n or n.startswith("ownership") or n.startswith("primary_doc")):
            xml_name = it["name"]
            break
    if not xml_name:
        for it in items:
            if (it.get("name") or "").lower().endswith(".xml"):
                xml_name = it["name"]
                break
    if not xml_name:
        return []
    r = _http_get(f"{base}/{xml_name}")
    if not r or r.status_code != 200:
        return []
    xml = r.text

    # Lightweight regex parsing avoids dragging lxml as a hard dep. Form 4 XML
    # is shallow enough that the targeted extractions below are stable.
    def _first(pattern: str, src: str = xml, flags=re.IGNORECASE | re.DOTALL) -> str | None:
        m = re.search(pattern, src, flags)
        return m.group(1).strip() if m else None

    name = _first(r"<rptOwnerName>(.*?)</rptOwnerName>")
    is_dir = _first(r"<isDirector>(\d)</isDirector>")
    is_off = _first(r"<isOfficer>(\d)</isOfficer>")
    is_ten = _first(r"<isTenPercentOwner>(\d)</isTenPercentOwner>")
    title = _first(r"<officerTitle>(.*?)</officerTitle>") or ""
    role_seed = title or ("Director" if is_dir == "1" else
                          "10% Owner" if is_ten == "1" else
                          "Other Officer" if is_off == "1" else "Other")
    role = _normalize_role(role_seed)

    # Filing-level Rule 10b5-1 affirmation (SEC 2023 Form 4 amendment): when
    # <aff10b5One>1</aff10b5One> is set, every transaction in this filing was
    # made under a 10b5-1 plan. This catches issuers (e.g. Alphabet/GOOGL) who
    # disclose via the SEC checkbox rather than per-transaction footnote prose.
    aff = (_first(r"<aff10b5One>\s*([\w\.]+)\s*</aff10b5One>") or "").strip().lower()
    filing_is_10b5_1 = aff in ("1", "true")

    out = []
    # nonDerivativeTransaction blocks
    for idx, block in enumerate(re.findall(
            r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
            xml, re.IGNORECASE | re.DOTALL)):
        code = (re.search(r"<transactionCode>(.)</transactionCode>", block) or [None, None])[1] \
                if False else None
        m = re.search(r"<transactionCode>(.)</transactionCode>", block, re.IGNORECASE)
        code = m.group(1) if m else None
        m = re.search(r"<transactionDate>.*?<value>(.*?)</value>", block, re.IGNORECASE | re.DOTALL)
        tdate = m.group(1) if m else None
        m = re.search(r"<transactionShares>.*?<value>(.*?)</value>", block, re.IGNORECASE | re.DOTALL)
        shares = _num(m.group(1)) if m else None
        m = re.search(r"<transactionPricePerShare>.*?<value>(.*?)</value>", block, re.IGNORECASE | re.DOTALL)
        price = _num(m.group(1)) if m else None
        m = re.search(r"<transactionAcquiredDisposedCode>.*?<value>(.)</value>", block, re.IGNORECASE | re.DOTALL)
        ad = m.group(1) if m else None
        # Footnotes referenced by transactionCoding or transactionAmounts:
        # they appear inside <footnoteId id="F1"/> tags. Pull every footnote
        # text from the document and concatenate the referenced ones.
        ref_ids = re.findall(r'<footnoteId\s+id="(F\d+)"\s*/>', block)
        footnote_text = ""
        for fid in ref_ids:
            fm = re.search(rf'<footnote\s+id="{fid}">(.*?)</footnote>', xml,
                            re.IGNORECASE | re.DOTALL)
            if fm:
                footnote_text += " " + re.sub(r"<.*?>", "", fm.group(1))
        fn = footnote_text.strip()
        is10 = _looks_10b5_1(fn) or filing_is_10b5_1
        out.append({
            "insider":   name or "Unknown",
            "role":      role,
            "tx_code":   code,
            "ad":        ad,                # A=acquired, D=disposed
            "is_acquisition": (ad == "A") if ad else None,
            "txn_index": idx,               # ordinal within the filing
            "date":      tdate,
            "shares":    shares,
            "price":     price,
            "value":     (shares * price) if (shares is not None and price is not None) else None,
            "footnote":  fn,
            "is_10b5_1": is10,
            "is_directional_signal": _is_directional_signal(code, ad, is10),
        })
    return out


def fetch_insider_transactions(ticker: str, lookback_days: int = 90) -> list[dict]:
    """Public entry point -- best-effort. Returns [] on any failure path."""
    cik = _resolve_cik(ticker)
    if not cik:
        return []
    try:
        filings = _fetch_form4_filings(cik, lookback_days)
    except Exception as exc:
        log.debug("Form 4 index failed for %s: %s", ticker, exc)
        return []
    txns = []
    for f in filings[:60]:    # safety cap
        try:
            txns.extend(_parse_form4_xml(cik, f["accession"]))
            time.sleep(0.12)   # be polite to EDGAR
        except Exception as exc:
            log.debug("Form 4 parse %s/%s failed: %s", ticker, f["accession"], exc)
    return txns


def _classify_insider_cluster(txns: list[dict]) -> str:
    if not txns:
        return "NO_ACTIVITY"
    cutoff = (datetime.utcnow().date() - timedelta(days=30)).isoformat()
    recent = [t for t in txns if (t.get("date") or "") >= cutoff]
    discretionary = [t for t in recent if not t.get("is_10b5_1")]
    # Only directional codes drive insider clusters (see _is_directional_signal
    # and the 10b5-1 spot test): P open-market buy, S open-market sale, and V
    # voluntary non-10b5-1 transaction (direction from acquired/disposed).
    # Grants (A), tax withholding (F), gifts (G) and derivative exercises (M/X)
    # are excluded -- they are mechanical, not sentiment.
    buys  = [t for t in discretionary
             if t.get("tx_code") == "P"
             or (t.get("tx_code") == "V" and t.get("ad") == "A")]
    sells = [t for t in discretionary
             if t.get("tx_code") == "S"
             or (t.get("tx_code") == "V" and t.get("ad") == "D")]
    buy_insiders  = {t["insider"] for t in buys}
    sell_insiders = {t["insider"] for t in sells}
    buy_dollars  = sum((t.get("value") or 0) for t in buys)
    sell_dollars = sum((t.get("value") or 0) for t in sells)
    has_buy_cluster  = len(buy_insiders) >= 3
    has_sell_cluster = len(sell_insiders) >= 3
    # If both sides have clusters, the dominant side (by dollar value) wins.
    if has_buy_cluster and has_sell_cluster:
        return ("STRONG_INSIDER_SELLING" if sell_dollars > buy_dollars
                else "STRONG_INSIDER_BUYING")
    if has_buy_cluster:
        return "STRONG_INSIDER_BUYING"
    if has_sell_cluster:
        return "STRONG_INSIDER_SELLING"
    if any((t.get("value") or 0) > 1_000_000 for t in discretionary):
        return "SINGLE_LARGE_TRANSACTION"
    if discretionary:
        return "WEAK_SIGNAL"
    return "NO_ACTIVITY"


_ROLE_WEIGHT_BUY = {"CEO": 3.0, "CFO": 2.5, "COO": 2.0, "Director": 1.5,
                    "10% Owner": 1.0, "Other Officer": 0.7, "Other": 0.5}
_ROLE_WEIGHT_SELL = {"CFO": 3.0, "CEO": 2.0, "COO": 1.8, "Director": 0.8,
                     "10% Owner": 1.0, "Other Officer": 0.7, "Other": 0.5}


def _weighted_insider_score(txns: list[dict]) -> tuple[float, int, int, float]:
    """Returns (score, n_discretionary, n_10b5_1, net_dollar)."""
    score = 0.0
    # Narrow to codes that reflect a real, discretionary view: open-market buy
    # (P), open-market sale (S), voluntary non-10b5-1 transaction (V). Gifts
    # (G), tax withholding (F), grants (A) and derivative exercises (M/X) are
    # not directional signals -- see the 10b5-1 spot test. We require shares
    # but NOT price > 0: multi-lot sales routinely report a 0 price in the XML
    # and disclose the weighted-average price in a footnote (verified against
    # real Lockheed Martin Form 4 filings) -- excluding them would silently
    # drop genuine insider sales. A price-0 row contributes at the floor
    # dollar-magnitude rather than vanishing.
    relevant = [t for t in txns
                if t.get("tx_code") in ("P", "S", "V")
                and (t.get("shares") or 0) > 0]
    n_10 = sum(1 for t in relevant if t.get("is_10b5_1"))
    n_dis = len(relevant) - n_10
    net_dollar = 0.0
    for t in relevant:
        if t.get("is_10b5_1"):
            w_scale = 0.2     # 5x less weight than discretionary
        else:
            w_scale = 1.0
        role = t.get("role") or "Other"
        code = t.get("tx_code")
        ad = t.get("ad")
        is_buy = code == "P" or (code == "V" and ad == "A")
        is_sell = code == "S" or (code == "V" and ad == "D")
        if not (is_buy or is_sell):
            continue
        role_w = (_ROLE_WEIGHT_BUY if is_buy else _ROLE_WEIGHT_SELL).get(role, 0.5)
        val = t.get("value") or 0
        # Magnitude on a log scale so a single $50M trade doesn't blow out.
        import math
        mag = math.log10(max(1.0, val)) - 4   # ~0 at $10K, ~3 at $10M
        contribution = role_w * w_scale * max(0.5, mag)
        score += contribution if is_buy else -contribution
        net_dollar += (val if is_buy else -val)
    # Normalize to -100..100 with a soft cap.
    score = max(-100, min(100, score * 6))
    return round(score, 2), n_dis, n_10, round(net_dollar, 0)


def _post_earnings_summary(txns: list[dict], earnings_date: str | None) -> str:
    if not earnings_date:
        return ""
    try:
        ed = datetime.fromisoformat(earnings_date[:10]).date()
    except Exception:
        return ""
    post = []
    for t in txns:
        try:
            td = datetime.fromisoformat((t.get("date") or "")[:10]).date()
        except Exception:
            continue
        if 0 <= (td - ed).days <= 30 and t.get("is_directional_signal"):
            code = t.get("tx_code")
            is_buy = code == "P" or (code == "V" and t.get("ad") == "A")
            side = "bought" if is_buy else "sold"
            v = t.get("value") or 0
            post.append(f"{t['role']} {t['insider']} {side} ${v:,.0f} on {td}")
    if not post:
        return "no discretionary insider activity in 30 days after last earnings"
    return "; ".join(post[:5])


def analyze_insider_activity(ticker: str, earnings_date: str | None) -> dict:
    txns = fetch_insider_transactions(ticker, lookback_days=90)
    if not txns:
        return {"score": 0.0, "strength": 0.0,
                "cluster": "NO_ACTIVITY", "recent": [],
                "n_10b5_1": 0, "n_discretionary": 0,
                "post_earnings": "no data"}
    cluster = _classify_insider_cluster(txns)
    score, n_dis, n_10, net_dollar = _weighted_insider_score(txns)
    cluster_multiplier = {
        "STRONG_INSIDER_BUYING": 1.5, "STRONG_INSIDER_SELLING": 1.5,
        "SINGLE_LARGE_TRANSACTION": 1.0, "WEAK_SIGNAL": 0.5,
        "NO_ACTIVITY": 0.0,
    }.get(cluster, 0.5)
    score = round(max(-100, min(100, score * cluster_multiplier)), 2)
    unique_insiders = len({t["insider"] for t in txns})
    strength = 30 + min(40, unique_insiders * 10)
    if n_dis > n_10:
        strength = min(100, strength + 15)
    else:
        strength = max(0, strength - 15)
    _SIDE = {"P": "buy", "S": "sell", "A": "grant", "G": "gift",
             "F": "tax_withholding", "M": "option_exercise"}

    def _txn_side(t: dict) -> str:
        if t.get("tx_code") == "V":
            return "voluntary_buy" if t.get("ad") == "A" else "voluntary_sell"
        return _SIDE.get(t.get("tx_code"), "other")

    recent = [{
        "insider": t["insider"], "role": t["role"], "date": t.get("date"),
        "side": _txn_side(t),
        "directional": bool(t.get("is_directional_signal")),
        "value": t.get("value"), "is_10b5_1": t.get("is_10b5_1"),
    } for t in txns[:15]]
    return {
        "score":            score,
        "strength":         strength,
        "cluster":          cluster,
        "recent":           recent,
        "n_discretionary":  n_dis,
        "n_10b5_1":         n_10,
        "net_dollar":       net_dollar,
        "post_earnings":    _post_earnings_summary(txns, earnings_date),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SUB-MODULE 3 -- SHORT INTEREST & SQUEEZE DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

def _yfinance_short_data(ticker: str) -> dict:
    """Fallback when FINRA fetch fails: pull yfinance .info fields."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return {}
    return {
        "shares_short":           _num(info.get("sharesShort")),
        "short_pct_of_float":     _num(info.get("shortPercentOfFloat")) and
                                   round(_num(info.get("shortPercentOfFloat")) * 100, 3),
        "short_ratio":            _num(info.get("shortRatio")),
        "shares_short_prior_month": _num(info.get("sharesShortPriorMonth")),
        "avg_daily_volume":       _num(info.get("averageVolume")),
        "source":                 "yfinance",
    }


def _finra_reg_sho_daily(ticker: str, lookback_days: int = 15) -> list[dict]:
    """Daily Reg-SHO short volume from FINRA cdn. Returns recent rows for ticker."""
    rows = []
    today = datetime.utcnow().date()
    fetched = 0
    for back in range(1, lookback_days + 5):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        url = f"{FINRA_REG_SHO_BASE}/CNMSshvol{d.strftime('%Y%m%d')}.txt"
        r = _http_get(url, timeout=15)
        if not r or r.status_code != 200:
            continue
        fetched += 1
        for line in r.text.splitlines():
            parts = line.split("|")
            if len(parts) < 5:
                continue
            if parts[1].strip().upper() == ticker.upper():
                rows.append({
                    "date":                d.isoformat(),
                    "short_volume":        _num(parts[2]),
                    "short_exempt_volume": _num(parts[3]),
                    "total_volume":        _num(parts[4]),
                })
                break
        if fetched >= lookback_days:
            break
    return rows


def analyze_short_interest(ticker: str, recent_price_pct_5d: float | None) -> dict:
    daily = _finra_reg_sho_daily(ticker, lookback_days=15)
    info = _yfinance_short_data(ticker)
    short_pct_float = info.get("short_pct_of_float")
    dtc = info.get("short_ratio")     # yfinance: days-to-cover-equivalent

    # Compute a trend direction from FINRA daily short_volume / total_volume.
    trend = "unknown"
    if len(daily) >= 6:
        ratios = [(_safe_div(d["short_volume"], d["total_volume"]) or 0)
                   for d in daily if d.get("total_volume")]
        if len(ratios) >= 6:
            recent_avg = sum(ratios[:3]) / 3
            prior_avg  = sum(ratios[-3:]) / 3
            if recent_avg > prior_avg * 1.10:
                trend = "building"
            elif recent_avg < prior_avg * 0.90:
                trend = "covering"
            else:
                trend = "stable"

    # Squeeze potential. Spec: base = short_pct_of_float * days_to_cover,
    # amplified by recent positive catalyst / uptrend.
    base = ((short_pct_float or 0) * (dtc or 0)) / 4   # crude normalization
    squeeze = min(100, max(0, base))
    if recent_price_pct_5d is not None and recent_price_pct_5d > 2.0:
        squeeze = min(100, squeeze + 15)

    # Signal score: "bullish-from-contrarian-standpoint" framing per spec.
    score = 0.0
    if trend == "covering":  score += 35
    if trend == "building":  score -= 35
    if (short_pct_float or 0) >= 25 and trend == "covering":
        score += 25
    elif (short_pct_float or 0) >= 25 and trend == "building":
        score -= 10   # bearish positioning but squeeze risk grows
    score = max(-100, min(100, round(score, 2)))

    # Strength: higher if SI is meaningful + data is recent.
    strength = 0
    if (short_pct_float or 0) >= 5: strength += 35
    if (short_pct_float or 0) >= 15: strength += 25
    if daily: strength += 25     # FINRA data present
    if (short_pct_float or 0) < 2: strength = max(0, strength - 30)
    strength = min(100, strength)

    return {
        "score":              score,
        "strength":           strength,
        "short_pct_of_float": short_pct_float,
        "days_to_cover":      dtc,
        "squeeze_potential":  round(squeeze, 1),
        "trend":              trend,
        "finra_days_fetched": len(daily),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SUB-MODULE 4 -- ANALYST REVISION TRACKER (Finnhub)
# ══════════════════════════════════════════════════════════════════════════════

def _finnhub(endpoint: str, params: dict) -> object | None:
    if not FINNHUB_API_KEY:
        return None
    params = {**params, "token": FINNHUB_API_KEY}
    r = _http_get(f"https://finnhub.io/api/v1/{endpoint}", params=params)
    if not r or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def analyze_revisions(ticker: str) -> dict:
    rec = _finnhub("stock/recommendation", {"symbol": ticker}) or []
    upd = _finnhub("stock/upgrade-downgrade", {"symbol": ticker}) or []
    if not (rec or upd):
        return {"score": 0.0, "strength": 0.0,
                "velocity": "NEUTRAL", "net_revision_count": 0}

    cutoff_30 = (datetime.utcnow().date() - timedelta(days=30)).isoformat()
    cutoff_7  = (datetime.utcnow().date() - timedelta(days=7)).isoformat()

    # upgrade/downgrade entries have fromGrade / toGrade strings.
    actions = []
    for u in upd:
        td = u.get("gradeTime")
        try:
            d = datetime.utcfromtimestamp(int(td)).date().isoformat() if td else u.get("date")
        except (TypeError, ValueError):
            d = u.get("date")
        a = (u.get("action") or "").lower()
        if "up" in a:
            actions.append((d, "up"))
        elif "down" in a:
            actions.append((d, "down"))
        elif "init" in a:
            actions.append((d, "init"))
    actions.sort(reverse=True)
    last7 = [a for a in actions if (a[0] or "") >= cutoff_7]
    last30 = [a for a in actions if (a[0] or "") >= cutoff_30]
    ups = sum(1 for _, k in last30 if k == "up")
    downs = sum(1 for _, k in last30 if k == "down")
    net = ups - downs

    # Velocity per spec.
    if len(last7) >= 5 and sum(1 for _, k in last7 if k == "up") >= len(last7) * 0.7:
        velocity = "STRONG_POSITIVE"
    elif len(last7) >= 5 and sum(1 for _, k in last7 if k == "down") >= len(last7) * 0.7:
        velocity = "STRONG_NEGATIVE"
    elif len(last7) >= 3 and len({k for _, k in last7}) == 1:
        velocity = "DIRECTIONAL_CLUSTER"
    else:
        velocity = "NEUTRAL"

    # Score
    if velocity == "STRONG_POSITIVE":
        score = 75
    elif velocity == "STRONG_NEGATIVE":
        score = -75
    elif net >= 3:
        score = 35
    elif net <= -3:
        score = -35
    else:
        score = max(-25, min(25, net * 10))
    score = round(score, 2)

    # Strength: higher when many analysts cover; rec table has rows of
    # {buy, hold, sell, strongBuy, strongSell, period}.
    n_analysts = 0
    if rec:
        latest = rec[0]
        n_analysts = sum(_num(latest.get(k)) or 0
                          for k in ("buy", "hold", "sell", "strongBuy", "strongSell"))
    strength = 30 + min(50, int(n_analysts) * 5)
    if n_analysts <= 2:
        strength = max(0, strength - 30)

    return {
        "score":               score,
        "strength":            strength,
        "velocity":            velocity,
        "net_revision_count":  net,
        "ups_30d":             ups,
        "downs_30d":           downs,
        "n_analysts":          int(n_analysts),
    }


# ══════════════════════════════════════════════════════════════════════════════
# CONGRESSIONAL TRADING ENRICHMENT
# ══════════════════════════════════════════════════════════════════════════════

def _load_congress_cache(url: str, name: str) -> list[dict]:
    cache = CONGRESS_CACHE_DIR / f"{name}.json"
    stale = True
    if cache.exists():
        age_h = (time.time() - cache.stat().st_mtime) / 3600
        stale = age_h > CONGRESS_CACHE_TTL_HOURS
        if not stale:
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except Exception:
                pass
    r = _http_get(url, timeout=60)
    if r and r.status_code == 200:
        try:
            data = r.json()
            cache.write_text(json.dumps(data), encoding="utf-8")
            return data
        except ValueError:
            return []
    # Fall back to a stale cache rather than nothing.
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def check_congressional_trades(ticker: str, lookback_days: int = 60) -> list[dict]:
    out = []
    cutoff = (datetime.utcnow().date() - timedelta(days=lookback_days)).isoformat()
    for url, src in [(SENATE_WATCHER_URL, "senate"), (HOUSE_WATCHER_URL, "house")]:
        rows = _load_congress_cache(url, src)
        if not isinstance(rows, list):
            continue
        for r in rows:
            t = (r.get("ticker") or r.get("symbol") or "").upper()
            if t != ticker.upper():
                continue
            d = (r.get("transaction_date") or r.get("disclosure_date")
                 or r.get("date") or "")[:10]
            if d and d < cutoff:
                continue
            out.append({
                "chamber":  src,
                "name":     r.get("senator") or r.get("representative") or r.get("name"),
                "party":    r.get("party"),
                "type":     r.get("type") or r.get("transaction_type"),
                "amount":   r.get("amount") or r.get("amount_range") or r.get("range"),
                "date":     d or None,
            })
    return out[:25]


# ══════════════════════════════════════════════════════════════════════════════
# SYNTHESIS LAYER -- pattern classification + composite score
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_signal(score: float | None, strength: float | None) -> int:
    s, st = _num(score) or 0, _num(strength) or 0
    if s > 30 and st > 50:
        return 1
    if s < -30 and st > 50:
        return -1
    return 0


def classify_pattern(E: dict, I: dict, S: dict, R: dict) -> str:
    """Map (E, I, S, R) triples to one of the 10 patterns from spec."""
    e, i, s_, r = (_normalize_signal(x["score"], x["strength"])
                   for x in (E, I, S, R))

    short_trend = (S.get("trend") or "").lower()
    short_pct = _num(S.get("short_pct_of_float")) or 0
    insider_score = _num(I.get("score")) or 0

    # FULL_CONVICTION_LONG / SHORT
    if (e, i, s_, r) == (1, 1, 1, 1):
        return "FULL_CONVICTION_LONG"
    if (e, i, s_, r) == (-1, -1, -1, -1):
        return "FULL_CONVICTION_SHORT"

    # INSIDER_SMOKE_SIGNAL -- public bullish (earnings beat) but insiders selling discretionary.
    if e >= 0 and i == -1 and insider_score < -40:
        return "INSIDER_SMOKE_SIGNAL"

    # VALUE_TRAP_WARNING -- beat reported but smart money bearish across channels.
    if e == 1 and i == -1 and short_trend == "building" and r == -1:
        return "VALUE_TRAP_WARNING"

    # SHORT_SQUEEZE_SETUP -- bullish fundamentals + heavy short positioning.
    if e == 1 and i == 1 and short_trend == "building" and r == 1 and short_pct >= 15:
        return "SHORT_SQUEEZE_SETUP"

    # CONTRARIAN_SETUP -- miss but insiders buying into weakness.
    if e == -1 and i == 1 and short_trend == "building":
        return "CONTRARIAN_SETUP"

    # CAPITULATION_BOTTOM -- bad miss + heavy insider buying + extreme SI.
    if (e == -1 and i == 1 and (_num(I.get("strength")) or 0) > 70
            and short_pct >= 20 and r == -1):
        return "CAPITULATION_BOTTOM"

    # QUIET_ACCUMULATION -- no earnings event but insiders + shorts + estimates positive.
    if e == 0 and i == 1 and short_trend == "covering" and r == 1:
        return "QUIET_ACCUMULATION"

    # QUIET_DISTRIBUTION
    if e == 0 and i == -1 and short_trend == "building" and r == -1:
        return "QUIET_DISTRIBUTION"

    return "MIXED_SIGNALS"


def compute_composite(pattern: str, E: dict, I: dict, S: dict, R: dict) -> float:
    """Per-pattern weighting per spec."""
    es, is_, ss, rs = (_num(x["score"]) or 0 for x in (E, I, S, R))

    if pattern in ("FULL_CONVICTION_LONG", "FULL_CONVICTION_SHORT"):
        return round(0.25 * es + 0.25 * is_ + 0.25 * ss + 0.25 * rs, 2)
    if pattern == "INSIDER_SMOKE_SIGNAL":
        # Insiders dominate when public narrative diverges.
        return round(0.17 * es + 0.50 * is_ + 0.17 * ss + 0.16 * rs, 2)
    if pattern == "SHORT_SQUEEZE_SETUP":
        # Bounded; squeezes have asymmetric upside but high downside.
        raw = 0.25 * es + 0.25 * is_ + 0.25 * ss + 0.25 * rs
        return round(max(-60, min(60, raw)), 2)
    if pattern == "VALUE_TRAP_WARNING":
        # Bearish signals collectively weight 0.6.
        return round(0.20 * es + 0.30 * is_ + 0.30 * ss + 0.20 * rs, 2)
    if pattern in ("QUIET_ACCUMULATION", "QUIET_DISTRIBUTION"):
        # Require strength > 60 on contributing signals; spec; we compute the
        # weighted sum either way and let the LOW-confidence gate handle it.
        return round(0.10 * es + 0.40 * is_ + 0.30 * ss + 0.20 * rs, 2)
    if pattern == "CAPITULATION_BOTTOM":
        return round(0.10 * es + 0.55 * is_ + 0.25 * ss + 0.10 * rs, 2)
    if pattern == "CONTRARIAN_SETUP":
        return round(0.20 * es + 0.45 * is_ + 0.20 * ss + 0.15 * rs, 2)
    # MIXED_SIGNALS fallback
    return round((es + is_ + ss + rs) / 4, 2)


def confidence_label(E, I, S, R) -> str:
    n_strong = sum(1 for x in (E, I, S, R) if (_num(x["strength"]) or 0) > 50)
    if n_strong >= 4:
        return "HIGH"
    if n_strong >= 2:
        return "MEDIUM"
    return "LOW"


# ══════════════════════════════════════════════════════════════════════════════
# INTEL NOTE (Groq) -- strict closed-context with [DATA REF] citations
# ══════════════════════════════════════════════════════════════════════════════

_INTEL_SYSTEM = (
    "You are a senior equity analyst writing a Smart Money Intelligence note. "
    "You may ONLY cite numbers and quotes from the DATA section. You may NOT "
    "introduce facts from training data. Every specific claim must be "
    "traceable. Use cautious, research-grade language. Never make explicit "
    "recommendations. Use phrases like 'data suggests', 'pattern indicates', "
    "'review-worthy'. Never use the words 'buy', 'sell', 'must', or 'recommend you'. "
    "Every specific numeric claim must be immediately followed by a "
    "[DATA REF: key_name] tag whose key appears in the DATA dictionary."
)


def _build_intel_data(ticker, pattern, composite, confidence, E, I, S, R, congress):
    """Flat data dictionary keyed in lowercase snake_case so factcheck_agent can
    verify every numeric claim in the LLM output."""
    return {
        "ticker":                       ticker,
        "pattern_detected":             pattern,
        "composite_score":              composite,
        "confidence_level":             confidence,

        "earnings_signal_score":        E["score"],
        "earnings_signal_strength":     E["strength"],
        "earnings_verdict":             E.get("verdict"),
        "earnings_last_report_date":    str(E.get("last_report_date") or ""),
        "earnings_transcript_quality":  E.get("transcript_quality"),
        "earnings_tone_ceo":            (E.get("tone_summary") or {}).get("tone_ceo"),
        "earnings_tone_cfo":            (E.get("tone_summary") or {}).get("tone_cfo"),
        "earnings_tone_qa":             (E.get("tone_summary") or {}).get("tone_qa"),
        "earnings_deflections":         (E.get("tone_summary") or {}).get("deflections"),

        "insider_signal_score":         I["score"],
        "insider_signal_strength":      I["strength"],
        "insider_cluster":              I.get("cluster"),
        "insider_discretionary_count":  I.get("n_discretionary"),
        "insider_10b5_1_count":         I.get("n_10b5_1"),
        "insider_net_dollar":           I.get("net_dollar"),
        "insider_post_earnings":        I.get("post_earnings"),

        "short_signal_score":           S["score"],
        "short_signal_strength":        S["strength"],
        "short_pct_of_float":           S.get("short_pct_of_float"),
        "short_days_to_cover":          S.get("days_to_cover"),
        "short_squeeze_potential":      S.get("squeeze_potential"),
        "short_trend":                  S.get("trend"),

        "revision_signal_score":        R["score"],
        "revision_signal_strength":     R["strength"],
        "revision_velocity":            R.get("velocity"),
        "revision_net_count":           R.get("net_revision_count"),
        "revision_n_analysts":          R.get("n_analysts"),

        "congressional_trades_count":   len(congress or []),
    }


def _build_intel_user_prompt(data, congress):
    """The user prompt formats the DATA section. Every numeric value is shown
    next to its EXACT data-ref key in snake_case so the LLM uses the right tag.
    factcheck_agent matches refs case-insensitively but exactly by key name."""
    # Build a "key: value" line per data field so the LLM can copy the key.
    refs = []
    for k, v in data.items():
        if v is None or isinstance(v, (dict, list)):
            continue
        refs.append(f"  - {k} = {v}")
    refs_block = "\n".join(refs)

    # Honest-disclosure requirement (Fix 2): when the earnings analysis rests
    # on a press release rather than a full call transcript, the note must say
    # so plainly -- this is disclosure, not a degradation flag.
    tq = data.get("earnings_transcript_quality") or ""
    pr_disclosure = ""
    if tq in ("substantive_press_release", "standard_press_release",
              "minimal_press_release"):
        pr_disclosure = (
            "TRANSCRIPT COVERAGE NOTE: the earnings analysis is based on the "
            "earnings press release filed via 8-K Item 2.02 -- a full call "
            "transcript was not publicly available, so Q&A pushback and live "
            "tone-shift analysis are NOT included. You MUST end the EARNINGS "
            "section with this exact sentence: \"Analysis based on earnings "
            "press release filed via 8-K Item 2.02. Full transcript not "
            "publicly available -- Q&A pushback analysis not included.\"")

    lines = [
        f"Smart Money Intelligence Analysis for {data['ticker']}:",
        "",
        "DATA SECTION (every numeric claim in your output must be immediately "
        "followed by a [DATA REF: key_name] tag whose key_name is one of the "
        "snake_case keys listed below -- do NOT invent keys, do NOT use display "
        "labels):",
        refs_block,
        "",
        f"PATTERN_DETECTED: {data['pattern_detected']}",
        f"COMPOSITE_SCORE: {data['composite_score']} (key: composite_score)",
        f"CONFIDENCE_LEVEL: {data['confidence_level']} (key: confidence_level)",
        "",
        f"EARNINGS CALL: score {data['earnings_signal_score']} "
            f"(key: earnings_signal_score), strength {data['earnings_signal_strength']} "
            f"(key: earnings_signal_strength), verdict {data['earnings_verdict']} "
            f"(key: earnings_verdict).",
        f"INSIDER ACTIVITY: score {data['insider_signal_score']} "
            f"(key: insider_signal_score), cluster {data['insider_cluster']} "
            f"(key: insider_cluster), discretionary {data['insider_discretionary_count']} "
            f"(key: insider_discretionary_count), 10b5-1 {data['insider_10b5_1_count']} "
            f"(key: insider_10b5_1_count), net dollar {data['insider_net_dollar']} "
            f"(key: insider_net_dollar).",
        f"SHORT INTEREST: score {data['short_signal_score']} "
            f"(key: short_signal_score), {data['short_pct_of_float']}% float "
            f"(key: short_pct_of_float), DTC {data['short_days_to_cover']} "
            f"(key: short_days_to_cover), trend {data['short_trend']} "
            f"(key: short_trend), squeeze {data['short_squeeze_potential']} "
            f"(key: short_squeeze_potential).",
        f"REVISIONS: score {data['revision_signal_score']} "
            f"(key: revision_signal_score), velocity {data['revision_velocity']} "
            f"(key: revision_velocity), net {data['revision_net_count']} "
            f"(key: revision_net_count), analysts {data['revision_n_analysts']} "
            f"(key: revision_n_analysts).",
        f"CONGRESSIONAL TRADES (60d): {data['congressional_trades_count']} "
            f"(key: congressional_trades_count).",
        "",
        pr_disclosure,
        "OUTPUT EXACTLY THESE SECTIONS, each as a clearly labelled heading:",
        "",
        "HEADLINE: One sentence describing the pattern detected and its implication.",
        "",
        "AGREEMENT_OR_DIVERGENCE_ANALYSIS: Are the four signals agreeing or disagreeing? "
        "Specify which signals diverge and what that divergence historically means.",
        "",
        "SMART_MONEY_VERDICT: What does the cumulative read across all four channels suggest? "
        "Cautious framing only.",
        "",
        "KEY_RISKS: What could invalidate this read? Address: tax-loss insider selling, "
        "hedging vs directional short positioning, lagging revisions.",
        "",
        "ADVISOR_QUESTIONS: Three questions for the advisor, never directives. Phrase as "
        "'Does X warrant review?' or 'Is the level of Y worth understanding before acting?'.",
        "",
        "RULES:",
        "1. Every specific number must be IMMEDIATELY followed by [DATA REF: key_name]",
        "   where key_name is one of the snake_case keys shown above (e.g. "
        "[DATA REF: composite_score], [DATA REF: insider_signal_score]).",
        "2. Do NOT use display labels in [DATA REF: ...] -- use only snake_case keys.",
        "3. Do not use the words buy, sell, must, or 'recommend you'.",
    ]
    return "\n".join(lines)


def _groq_intel_note(prompt: str) -> str | None:
    if not GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": _INTEL_SYSTEM},
                      {"role": "user", "content": prompt}],
            temperature=GROQ_TEMPERATURE, max_tokens=1600)
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as exc:
        log.warning("Groq intel note failed: %s", exc)
        return None


def _forbidden_language_check(text: str) -> list[str]:
    """Returns list of forbidden tokens found; empty if clean."""
    if not text:
        return []
    hits = []
    for pat in FORBIDDEN_PATTERNS:
        for m in pat.finditer(text):
            hits.append(m.group(0))
    return hits


# ══════════════════════════════════════════════════════════════════════════════
# Top-level orchestration per ticker
# ══════════════════════════════════════════════════════════════════════════════

def analyze_ticker(db, ticker: str, today: date) -> dict:
    """Runs all four sub-modules + synthesis + intel note for one ticker.
    Returns the row payload ready to upsert into smart_money_intel."""
    log.info("smart_money_intel: %s -- starting", ticker)
    transcript_pkg = fetch_transcript(db, ticker, today)
    E = analyze_earnings_call(transcript_pkg, ticker, today)
    earnings_date = E.get("last_report_date")
    I = analyze_insider_activity(ticker, earnings_date)

    # Recent 5-day price action for short squeeze amplification.
    recent_5d_pct = None
    try:
        hist = yf.download(ticker, period="10d", interval="1d",
                            auto_adjust=True, progress=False)
        if hist is not None and not hist.empty and len(hist) >= 6:
            closes = hist["Close"].dropna()
            if len(closes) >= 6:
                recent_5d_pct = float((closes.iloc[-1] / closes.iloc[-6] - 1) * 100)
    except Exception:
        recent_5d_pct = None

    S = analyze_short_interest(ticker, recent_5d_pct)
    R = analyze_revisions(ticker)
    congress = check_congressional_trades(ticker)

    pattern = classify_pattern(E, I, S, R)
    composite = compute_composite(pattern, E, I, S, R)
    confidence = confidence_label(E, I, S, R)

    intel_data = _build_intel_data(ticker, pattern, composite, confidence,
                                     E, I, S, R, congress)
    prompt = _build_intel_user_prompt(intel_data, congress)
    note = _groq_intel_note(prompt)

    verification = {"verification_score": None, "quality_grade": "UNVERIFIED"}
    if note:
        forbidden = _forbidden_language_check(note)
        if forbidden:
            log.warning("%s: intel note used forbidden language %s -- dropping note",
                        ticker, forbidden)
            note = None
            verification = {"verification_score": 0.0,
                             "quality_grade": "UNVERIFIED"}
        else:
            verification = factcheck(note, intel_data)
            note_meta = {"verification_score": verification.get("verification_score"),
                          "quality_grade": verification.get("quality_grade")}
            log.info("%s: intel note %d claims, %d verified -> %s",
                     ticker, verification["total_claims"],
                     verification["verified_claims"], verification["quality_grade"])
            verification = note_meta

    return {
        "ticker":                       ticker,
        "snapshot_date":                today.isoformat(),
        "run_type":                     "midday",   # caller overrides

        "earnings_signal_score":        E["score"],
        "earnings_signal_strength":     E["strength"],
        "earnings_last_report_date":    E.get("last_report_date"),
        "earnings_verdict":             E.get("verdict"),
        "earnings_tone_summary":        E.get("tone_summary"),
        "earnings_transcript_quality":  E.get("transcript_quality"),

        "insider_signal_score":         I["score"],
        "insider_signal_strength":      I["strength"],
        "insider_detected_cluster":     I.get("cluster"),
        "insider_recent_transactions":  I.get("recent"),
        "insider_10b5_1_count":         I.get("n_10b5_1") or 0,
        "insider_discretionary_count":  I.get("n_discretionary") or 0,
        "insider_post_earnings_summary": I.get("post_earnings"),

        "short_signal_score":           S["score"],
        "short_signal_strength":        S["strength"],
        "short_pct_of_float":           S.get("short_pct_of_float"),
        "days_to_cover":                S.get("days_to_cover"),
        "squeeze_potential_score":      S.get("squeeze_potential"),
        "short_trend_direction":        S.get("trend"),

        "revision_signal_score":        R["score"],
        "revision_signal_strength":     R["strength"],
        "revision_velocity":            R.get("velocity"),
        "net_revision_count":           R.get("net_revision_count"),

        "congressional_activity":       congress or None,

        "pattern_detected":             pattern,
        "composite_smart_money_score":  composite,
        "confidence_level":             confidence,

        "intel_note":                   note,
        "verification_score":           verification.get("verification_score"),
        "quality_grade":                verification.get("quality_grade"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Persistence + CLI
# ══════════════════════════════════════════════════════════════════════════════

def _db():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _persist(db, row: dict) -> None:
    try:
        db.table("smart_money_intel").upsert(
            row, on_conflict="ticker,snapshot_date,run_type").execute()
    except Exception as exc:
        log.warning("smart_money_intel upsert %s failed: %s", row.get("ticker"), exc)


def run(run_type: str = "midday", top_n: int = 30) -> None:
    db = _db()
    today = datetime.now(timezone.utc).date()
    log.info("Smart Money Intel -- run_type=%s, top_n=%d, date=%s",
             run_type, top_n, today)
    # Latest run_date for the requested run_type
    recent = (db.table("ranked_focus_list").select("run_date")
              .eq("run_type", run_type).order("run_date", desc=True)
              .limit(1).execute().data)
    if not recent:
        log.warning("no ranked_focus_list rows for run_type=%s", run_type)
        return
    run_date = recent[0]["run_date"]
    picks = (db.table("ranked_focus_list")
             .select("ticker,rank,conviction_grade")
             .eq("run_date", run_date).eq("run_type", run_type)
             .order("rank").limit(top_n).execute().data or [])
    if not picks:
        log.warning("no picks for %s/%s", run_date, run_type)
        return
    log.info("Running smart-money intel on %d picks from %s/%s",
             len(picks), run_date, run_type)

    rows = []
    pattern_counter = Counter()
    grade_caps = []   # for the summary
    for p in picks:
        try:
            row = analyze_ticker(db, p["ticker"], today)
            row["run_type"] = run_type
            _persist(db, row)
            rows.append(row)
            pattern_counter[row["pattern_detected"]] += 1
            if row["pattern_detected"] in ("INSIDER_SMOKE_SIGNAL",
                                            "VALUE_TRAP_WARNING",
                                            "FULL_CONVICTION_SHORT"):
                grade_caps.append((p["ticker"], row["pattern_detected"]))
        except Exception as exc:
            log.warning("%s: smart-money pipeline error: %s",
                        p["ticker"], type(exc).__name__)
            continue

    _report(rows, pattern_counter, grade_caps)


def _report(rows, patterns, caps):
    bar = "=" * 80
    print(f"\n{bar}\nSMART MONEY INTELLIGENCE -- {len(rows)} tickers analyzed\n{bar}")
    print("\n[ PATTERN DISTRIBUTION ]")
    for p, n in patterns.most_common():
        print(f"  {p:<25}{n}")
    print(f"\n[ GRADE-CAPPING PATTERNS DETECTED -- {len(caps)} ]")
    for t, p in caps:
        print(f"  {t:<8}{p}")
    # Show 3 most extreme composite scores
    extreme = sorted(rows, key=lambda r: abs(r.get("composite_smart_money_score") or 0),
                      reverse=True)[:3]
    print("\n[ TOP-3 BY |COMPOSITE| ]")
    for r in extreme:
        print(f"  {r['ticker']:<8}{r['pattern_detected']:<25}"
              f"composite={r['composite_smart_money_score']:>7}  "
              f"conf={r['confidence_level']:<7}  "
              f"quality={r['quality_grade']}")
    print(f"\n{bar}\n")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--ticker":
        if len(sys.argv) < 3:
            log.error("usage: --ticker AAPL")
            sys.exit(2)
        ticker = sys.argv[2].upper()
        db = _db()
        today = datetime.now(timezone.utc).date()
        row = analyze_ticker(db, ticker, today)
        print(json.dumps({
            k: v for k, v in row.items()
            if k not in ("insider_recent_transactions",
                          "earnings_tone_summary",
                          "congressional_activity")
        }, indent=2, default=str))
        if row.get("intel_note"):
            print("\n--- INTEL NOTE ---\n")
            print(row["intel_note"])
        return
    run_type = sys.argv[1] if len(sys.argv) > 1 else "midday"
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    run(run_type, top_n)


if __name__ == "__main__":
    main()
