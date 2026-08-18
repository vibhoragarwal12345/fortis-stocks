"""
Sentiment Scorer  (upgraded)
Financial sentiment scoring -- both a reusable library and a runnable agent.

LIBRARY API (unchanged -- imported by agents/crowd_intelligence.py)
------------------------------------------------------------------
    score_text(text)        -> float in [-1, +1]
    score_texts(iterable)   -> float in [-1, +1]   (mean over non-empty)
    score_many(iterable)    -> list[float]         (batched -- fast path)
    get_backend()           -> "finbert" | "lexicon"

Backends: ProsusAI/finbert via transformers when torch+transformers load,
otherwise a finance bull/bear lexicon. Chosen lazily; never crashes a caller.

AGENT  (python pipeline/processors/sentiment_scorer.py [run_type])
------------------------------------------------------------------
  STAGE 1  Score every unscored text source:
             news_items.sentiment_score              IS NULL
             ticker_debates.finbert_aggregated_score  IS NULL  (bull+bear text)
             sec_filings.sentiment_score              IS NULL  (description)
  STAGE 2  Per-ticker rollup into ticker_sentiment_rollup, applying:
             - recency weighting   (newer news counts for more)
             - source weighting    (Reuters/Bloomberg > aggregators)
  STAGE 3  Print top bullish / bearish / biggest mood-swing tickers.

Entity-level sentiment: a headline naming several companies ("Apple beats
but Intel warns") is split on contrast conjunctions and each clause's score
is attributed to the company named in it. spaCy (en_core_web_sm) NER, when
installed, corroborates the multi-company detection; ticker attribution
itself uses the company-name dictionary below.

Convention: positive = bullish, negative = bearish, 0.0 = neutral / unknown.
"""

import logging
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Finance lexicon (fallback backend)
# ══════════════════════════════════════════════════════════════════════════════

_BULL_WORDS = {
    "beat", "beats", "bullish", "buy", "buying", "upgrade", "upgraded",
    "outperform", "breakout", "surge", "surged", "rally", "rallied", "gain",
    "gains", "gained", "growth", "strong", "stronger", "soar", "soared",
    "jump", "jumped", "record", "profit", "profitable", "momentum",
    "undervalued", "long", "calls", "moon", "support", "rebound", "raise",
    "raised", "exceed", "exceeded", "optimistic", "accelerate", "expansion",
    "win", "wins", "winning", "positive", "boom", "uptrend", "green",
    "topped", "rocket", "squeeze", "outperforming", "upside",
    # third-person / inflected forms
    "surges", "soars", "jumps", "climbs", "climbed", "climbing", "rises",
    "rose", "rising", "tops", "pops", "popped", "spikes", "spiked", "rallies",
    "gaining", "beating", "raises", "wins",
}
_BEAR_WORDS = {
    "miss", "missed", "bearish", "sell", "selling", "downgrade", "downgraded",
    "underperform", "crash", "crashed", "plunge", "plunged", "drop", "dropped",
    "fall", "fell", "falling", "decline", "declined", "weak", "weaker",
    "loss", "losses", "warning", "warn", "warned", "cut", "cuts", "slump",
    "overvalued", "short", "puts", "dump", "dumping", "resistance", "selloff",
    "lawsuit", "investigation", "fraud", "bankruptcy", "default", "risk",
    "risky", "negative", "concern", "concerns", "slowdown", "downtrend",
    "red", "recall", "halt", "halted", "disappointing", "bear", "downside",
    # third-person / inflected forms
    "warns", "plunges", "drops", "falls", "slumps", "slides", "slid",
    "sinks", "sank", "sinking", "tumbles", "tumbled", "slips", "slipped",
    "misses", "declines", "cutting", "warning", "losing", "sliding",
}
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z'-]+")


class _LexiconBackend:
    name = "lexicon"

    def score(self, text: str) -> float:
        words = [w.lower() for w in _WORD_RE.findall(text or "")]
        if not words:
            return 0.0
        bull = sum(1 for w in words if w in _BULL_WORDS)
        bear = sum(1 for w in words if w in _BEAR_WORDS)
        if bull == 0 and bear == 0:
            return 0.0
        return (bull - bear) / (bull + bear)

    def score_many(self, texts) -> list[float]:
        return [self.score(t or "") for t in texts]


class _FinBertBackend:
    name = "finbert"

    def __init__(self):
        from transformers import pipeline  # raises if transformers/torch absent
        self._pipe = pipeline(
            "text-classification", model="ProsusAI/finbert",
            truncation=True, max_length=512,
        )

    @staticmethod
    def _to_score(out) -> float:
        label = str(out["label"]).lower()
        conf = float(out["score"])
        return conf if label == "positive" else -conf if label == "negative" else 0.0

    def score(self, text: str) -> float:
        if not text or not text.strip():
            return 0.0
        return self._to_score(self._pipe(text[:2000])[0])

    def score_many(self, texts) -> list[float]:
        """Batched scoring -- the fast path for thousands of texts."""
        clean = [(t or "")[:2000] for t in texts]
        scored = [0.0] * len(clean)
        idx = [i for i, t in enumerate(clean) if t.strip()]
        if not idx:
            return scored
        nonempty = [clean[i] for i in idx]
        for start in range(0, len(nonempty), 256):
            chunk = nonempty[start : start + 256]
            outs = self._pipe(chunk, batch_size=16)
            for j, o in enumerate(outs):
                scored[idx[start + j]] = self._to_score(o)
        return scored


_backend = None


def _get_backend():
    global _backend
    if _backend is None:
        try:
            _backend = _FinBertBackend()
            log.info("sentiment_scorer: FinBERT backend ready")
        except Exception as exc:
            _backend = _LexiconBackend()
            log.info("sentiment_scorer: FinBERT unavailable (%s) -- lexicon backend",
                     type(exc).__name__)
    return _backend


# ── Library API ─────────────────────────────────────────────────────────────────

def score_text(text: str) -> float:
    """Score a single block of text. Returns a float in [-1.0, +1.0]."""
    try:
        return round(float(_get_backend().score(text or "")), 4)
    except Exception:
        return 0.0


def score_texts(texts) -> float:
    """Mean sentiment over an iterable of texts (empty entries ignored)."""
    vals = [score_text(str(t)) for t in (texts or []) if t and str(t).strip()]
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def score_many(texts) -> list[float]:
    """Batched scoring of many texts -- much faster than score_text in a loop."""
    try:
        return [round(float(v), 4) for v in _get_backend().score_many(list(texts))]
    except Exception:
        return [0.0] * len(list(texts))


def get_backend() -> str:
    """Return the active backend name: 'finbert' or 'lexicon'."""
    return _get_backend().name


# ══════════════════════════════════════════════════════════════════════════════
# Entity-level sentiment
# ══════════════════════════════════════════════════════════════════════════════

# Company name -> ticker. Best-effort map of names that commonly co-occur in
# multi-company headlines. Attribution of clause sentiment relies on this map;
# spaCy NER (below) only corroborates that a headline names several companies.
_COMPANY_TO_TICKER = {
    "apple": "AAPL", "microsoft": "MSFT", "amazon": "AMZN", "alphabet": "GOOGL",
    "google": "GOOGL", "meta": "META", "facebook": "META", "nvidia": "NVDA",
    "tesla": "TSLA", "netflix": "NFLX", "intel": "INTC", "amd": "AMD",
    "broadcom": "AVGO", "qualcomm": "QCOM", "micron": "MU", "oracle": "ORCL",
    "salesforce": "CRM", "adobe": "ADBE", "cisco": "CSCO", "ibm": "IBM",
    "palantir": "PLTR", "snowflake": "SNOW", "servicenow": "NOW", "uber": "UBER",
    "lyft": "LYFT", "airbnb": "ABNB", "coinbase": "COIN", "paypal": "PYPL",
    "block": "SQ", "shopify": "SHOP", "spotify": "SPOT", "disney": "DIS",
    "comcast": "CMCSA", "at&t": "T", "verizon": "VZ", "boeing": "BA",
    "lockheed martin": "LMT", "general motors": "GM", "ford": "F",
    "general electric": "GE", "caterpillar": "CAT", "deere": "DE",
    "jpmorgan": "JPM", "jp morgan": "JPM", "goldman sachs": "GS",
    "morgan stanley": "MS", "bank of america": "BAC", "wells fargo": "WFC",
    "citigroup": "C", "visa": "V", "mastercard": "MA", "berkshire": "BRK.B",
    "exxon": "XOM", "exxonmobil": "XOM", "chevron": "CVX", "conocophillips": "COP",
    "pfizer": "PFE", "merck": "MRK", "johnson & johnson": "JNJ",
    "eli lilly": "LLY", "abbvie": "ABBV", "unitedhealth": "UNH", "moderna": "MRNA",
    "walmart": "WMT", "costco": "COST", "target": "TGT", "home depot": "HD",
    "lowe's": "LOW", "nike": "NKE", "starbucks": "SBUX", "mcdonald's": "MCD",
    "coca-cola": "KO", "pepsico": "PEP", "procter & gamble": "PG",
    "general mills": "GIS", "ge aerospace": "GE", "honeywell": "HON",
    "3m": "MMM", "dell": "DELL", "hp": "HPQ", "hewlett packard": "HPE",
    "marvell": "MRVL", "arm": "ARM", "super micro": "SMCI", "supermicro": "SMCI",
}

_CONTRAST_SPLIT = re.compile(
    r"\s+(?:but|while|whereas|however|though|although|amid|despite|as)\s+|[;]",
    re.IGNORECASE,
)


def _clauses(text: str) -> list[str]:
    return [p.strip() for p in _CONTRAST_SPLIT.split(text or "") if p and p.strip()]


# Clause-level scoring fallback. FinBERT goes neutral on ultra-short fragments
# ("Apple beats"), losing the signal; the keyword lexicon handles fragments
# reliably, so we defer to it whenever FinBERT returns ~neutral on a clause.
_clause_lexicon = _LexiconBackend()


def _score_clause(text: str) -> float:
    finbert = score_text(text)
    if abs(finbert) < 0.05:
        lex = round(_clause_lexicon.score(text), 4)
        if lex != 0.0:
            return lex
    return finbert


def _find_companies(text: str) -> dict[str, str]:
    """{ticker: matched_name} for known companies named in `text`."""
    low = (text or "").lower()
    found: dict[str, str] = {}
    for name, ticker in _COMPANY_TO_TICKER.items():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            found.setdefault(ticker, name)
    return found


class _NERBackend:
    """spaCy ORG detector -- corroborates multi-company headlines."""
    name = "spacy"

    def __init__(self):
        import spacy
        self._nlp = spacy.load("en_core_web_sm",
                               disable=["lemmatizer", "tagger"])

    def org_count(self, text: str) -> int:
        return sum(1 for e in self._nlp(text or "").ents if e.label_ == "ORG")


_ner = None
_ner_tried = False


def _get_ner():
    global _ner, _ner_tried
    if not _ner_tried:
        _ner_tried = True
        try:
            _ner = _NERBackend()
            log.info("sentiment_scorer: spaCy NER ready (en_core_web_sm)")
        except Exception as exc:
            _ner = None
            log.info("sentiment_scorer: spaCy unavailable (%s) -- entity split "
                     "uses the company-name dictionary only", type(exc).__name__)
    return _ner


def entity_sentiments(headline: str) -> dict[str, str | float]:
    """Per-ticker sentiment for a multi-company headline.

    Returns {} for single-company headlines (caller scores the whole headline).
    For multi-company headlines, splits on contrast conjunctions and attributes
    each clause's score to the company named in that clause.
    """
    found = _find_companies(headline)
    ner = _get_ner()
    multi = len(found) >= 2 or (ner is not None and ner.org_count(headline) >= 2
                                and len(found) >= 2)
    if not multi:
        return {}
    clauses = _clauses(headline)
    whole = score_text(headline)
    if len(clauses) < 2:
        return {}                       # several companies but one clause
    clause_score = {c: _score_clause(c) for c in clauses}
    result: dict[str, str | float] = {}
    for ticker, name in found.items():
        owning = next((c for c in clauses
                       if re.search(r"\b" + re.escape(name) + r"\b", c.lower())),
                      None)
        result[ticker] = clause_score[owning] if owning else whole
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Recency & source weighting
# ══════════════════════════════════════════════════════════════════════════════

def _recency_weight(published_at: datetime | None, now: datetime) -> float:
    if published_at is None:
        return 0.1
    age_h = (now - published_at).total_seconds() / 3600.0
    if age_h <= 2:
        return 1.0
    if age_h <= 6:
        return 0.8
    if age_h <= 12:
        return 0.5
    if age_h <= 24:
        return 0.3
    return 0.1


# tier -> (weight, keyword set). Matched against "<source> <headline>" so a
# Google-News item carrying a "- CNBC" byline still lands in the right tier.
_SOURCE_TIERS = (
    ("tier1", 1.0, {"reuters", "bloomberg", "wsj", "wall street journal",
                    "dow jones", "financial times", "the economist"}),
    ("tier2", 0.8, {"cnbc", "marketwatch", "market watch", "yahoo", "barron",
                    "forbes", "fortune", "business insider"}),
    ("tier3", 0.6, {"benzinga", "seeking alpha", "motley fool", "thestreet",
                    "investorplace", "zacks", "finnhub"}),
    ("aggregator", 0.4, {"google news", "google_news", "pr newswire",
                         "globe newswire", "businesswire", "accesswire",
                         "newsfile"}),
)


def _source_tier(source: str, headline: str) -> tuple[str, float]:
    """Classify a news item into a reliability tier and return (tier, weight)."""
    blob = f"{source or ''} {headline or ''}".lower()
    for tier, weight, keywords in _SOURCE_TIERS:
        if any(k in blob for k in keywords):
            return tier, weight
    s = (source or "").lower()
    if "yahoo" in s:
        return "tier2", 0.8
    if "finnhub" in s:
        return "tier3", 0.6
    if "google" in s:
        return "aggregator", 0.4
    return "tier2", 0.5                 # unknown -> neutral default


# ══════════════════════════════════════════════════════════════════════════════
# Agent -- database helpers
# ══════════════════════════════════════════════════════════════════════════════

BULL_THRESHOLD   = 0.15
BEAR_THRESHOLD   = -0.15
ROLLUP_WINDOW_H  = 24       # "current" sentiment window
BASELINE_DAYS    = 7        # velocity baseline window
MIN_REPORT_VOL   = 3        # min articles for a ticker to enter the top/bottom lists
UPDATE_WORKERS   = 8        # threads for per-row sentiment_score writes


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _fetch_all(query_fn, page: int = 1000, key: str = "id") -> list[dict]:
    """Page through a Supabase select (PostgREST caps a response at ~1000 rows).

    Uses keyset (cursor) pagination -- `order(key) + key > last_seen` -- NOT
    .range(). LIMIT/OFFSET makes Postgres walk and discard every row before the
    offset, so a full pass is O(n^2); once news_items passed ~500k rows that
    blew through Supabase's 2-minute statement_timeout and raised 57014,
    killing this agent silently every day from 2026-07-15 to 2026-08-17 and
    freezing ticker_sentiment_rollup. Keyset paging is constant-time per page
    and independent of table size.

    `key` must be selected by query_fn and be unique+ordered (the id PK).
    """
    rows: list[dict] = []
    last = None
    while True:
        q = query_fn().order(key).limit(page)
        if last is not None:
            q = q.gt(key, last)
        chunk = q.execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        if key not in chunk[-1]:
            raise KeyError(
                f"_fetch_all: keyset column {key!r} missing from the select list; "
                "add it to .select() or pass key=<column>")
        last = chunk[-1][key]


def _write_scores(db, table: str, score_col: str, updates: list[tuple[int, float]]) -> int:
    """Write (id, score) pairs back to `table`.`score_col` concurrently."""
    done = 0

    def _one(pair):
        rid, score = pair
        try:
            db.table(table).update({score_col: score}).eq("id", rid).execute()
            return True
        except Exception as exc:
            log.debug("update %s#%s failed: %s", table, rid, exc)
            return False

    with ThreadPoolExecutor(max_workers=UPDATE_WORKERS) as pool:
        for ok in pool.map(_one, updates):
            done += 1 if ok else 0
    return done


# ══════════════════════════════════════════════════════════════════════════════
# Agent -- Stage 1: score unscored sources
# ══════════════════════════════════════════════════════════════════════════════

def _score_news(db) -> None:
    # Only score rows that news_resolver has cleared. Rows tagged 'unresolved'
    # (ticker assignment failed verification) must not be re-scored -- their
    # sentiment_score was nulled deliberately.
    rows = _fetch_all(lambda: db.table("news_items")
                      .select("id,ticker,headline,source")
                      .is_("sentiment_score", "null")
                      .eq("resolution_status", "verified"))
    if not rows:
        log.info("news_items: nothing to score")
        return
    log.info("news_items: scoring %d unscored headlines...", len(rows))

    headlines = [r.get("headline") or "" for r in rows]
    scores = score_many(headlines)                         # batched whole-headline pass

    # Entity-level override for multi-company headlines.
    overrides = 0
    for i, r in enumerate(rows):
        ents = entity_sentiments(headlines[i])
        if ents and r.get("ticker") in ents:
            scores[i] = round(float(ents[r["ticker"]]), 4)
            overrides += 1
    log.info("news_items: %d headlines re-scored at clause level (multi-company)",
             overrides)

    written = _write_scores(db, "news_items", "sentiment_score",
                            [(r["id"], scores[i]) for i, r in enumerate(rows)])
    log.info("news_items: %d/%d sentiment scores written", written, len(rows))


def _score_debates(db) -> None:
    try:
        rows = _fetch_all(lambda: db.table("ticker_debates")
                          .select("id,bull_case,bear_case,consensus_tilt")
                          .is_("finbert_aggregated_score", "null"))
    except Exception as exc:
        log.warning("ticker_debates: skipped (%s)", exc)
        return
    if not rows:
        log.info("ticker_debates: nothing to score")
        return
    log.info("ticker_debates: scoring %d aggregated debate texts...", len(rows))
    texts = [" ".join(filter(None, (r.get("bull_case"), r.get("bear_case"),
                                    r.get("consensus_tilt")))) for r in rows]
    scores = score_many(texts)
    written = _write_scores(db, "ticker_debates", "finbert_aggregated_score",
                            [(r["id"], scores[i]) for i, r in enumerate(rows)])
    log.info("ticker_debates: %d/%d scores written", written, len(rows))


def _score_filings(db) -> None:
    try:
        rows = _fetch_all(lambda: db.table("sec_filings")
                          .select("id,description,form_type")
                          .is_("sentiment_score", "null"))
    except Exception as exc:
        log.warning("sec_filings: skipped -- column missing? (%s)", exc)
        return
    if not rows:
        log.info("sec_filings: nothing to score")
        return
    log.info("sec_filings: scoring %d filing descriptions...", len(rows))
    texts = [r.get("description") or r.get("form_type") or "" for r in rows]
    scores = score_many(texts)
    written = _write_scores(db, "sec_filings", "sentiment_score",
                            [(r["id"], scores[i]) for i, r in enumerate(rows)])
    log.info("sec_filings: %d/%d scores written", written, len(rows))


# ══════════════════════════════════════════════════════════════════════════════
# Agent -- Stage 2: per-ticker rollup
# ══════════════════════════════════════════════════════════════════════════════

def _build_rollups(db, run_type: str, now: datetime) -> list[dict]:
    since = (now - timedelta(days=BASELINE_DAYS)).isoformat()
    # Only verified rows enter the rollup; 'unresolved' rows are excluded so
    # mis-tagged news (e.g. VST Tillers under NYSE:VST) doesn't poison sentiment.
    rows = _fetch_all(lambda: db.table("news_items")
                      .select("id,ticker,headline,source,url,published_at,sentiment_score")
                      .gte("published_at", since)
                      .eq("resolution_status", "verified"))
    log.info("rollup: %d news items in the last %d days", len(rows), BASELINE_DAYS)

    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("ticker"):
            by_ticker[r["ticker"]].append(r)

    window_cut = now - timedelta(hours=ROLLUP_WINDOW_H)
    rollups: list[dict] = []

    for ticker, items in by_ticker.items():
        baseline_scores = [float(r["sentiment_score"]) for r in items
                           if r.get("sentiment_score") is not None]
        baseline = (round(sum(baseline_scores) / len(baseline_scores), 4)
                    if baseline_scores else 0.0)

        # "Current" window -- recency- & source-weighted.
        window = []
        for r in items:
            pub = _parse_dt(r.get("published_at"))
            if pub is not None and pub >= window_cut:
                window.append((r, pub))
        if not window:
            continue

        num = den = 0.0
        bullish = bearish = 0
        tier_scores: dict[str, list[float]] = defaultdict(list)
        scored_items = []
        for r, pub in window:
            s = r.get("sentiment_score")
            if s is None:
                continue
            s = float(s)
            rw = _recency_weight(pub, now)
            tier, sw = _source_tier(r.get("source"), r.get("headline"))
            w = rw * sw
            num += s * w
            den += w
            tier_scores[tier].append(s)
            if s > BULL_THRESHOLD:
                bullish += 1
            elif s < BEAR_THRESHOLD:
                bearish += 1
            scored_items.append({"headline": r.get("headline"), "score": round(s, 4),
                                  "source": r.get("source"), "url": r.get("url")})

        if den == 0 or not scored_items:
            continue
        weighted = max(-1.0, min(1.0, round(num / den, 4)))

        scored_items.sort(key=lambda x: x["score"], reverse=True)
        source_breakdown = {
            tier: {"avg_sentiment": round(sum(v) / len(v), 4), "count": len(v)}
            for tier, v in tier_scores.items()
        }

        rollups.append({
            "ticker":                ticker,
            "snapshot_date":         now.date().isoformat(),
            "run_type":              run_type,
            "weighted_sentiment":    weighted,
            "sentiment_volume":      len(scored_items),
            "sentiment_velocity":    round(weighted - baseline, 4),
            "bullish_article_count": bullish,
            "bearish_article_count": bearish,
            "top_3_bullish":         scored_items[:3],
            "top_3_bearish":         [x for x in reversed(scored_items[-3:])],
            "source_breakdown":      source_breakdown,
        })

    log.info("rollup: built %d ticker rollups", len(rollups))
    if rollups:
        try:
            db.table("ticker_sentiment_rollup").upsert(
                rollups, on_conflict="ticker,snapshot_date,run_type").execute()
            log.info("rollup: %d rows upserted into ticker_sentiment_rollup",
                     len(rollups))
        except Exception as exc:
            log.warning("rollup: upsert failed -- apply migration 006? (%s)", exc)
    return rollups


# ══════════════════════════════════════════════════════════════════════════════
# Agent -- Stage 3: report
# ══════════════════════════════════════════════════════════════════════════════

def _report(rollups: list[dict]) -> None:
    bar = "=" * 72
    print(f"\n{bar}\nTICKER SENTIMENT ROLLUP -- {len(rollups)} tickers\n{bar}")
    eligible = [r for r in rollups if r["sentiment_volume"] >= MIN_REPORT_VOL]
    print(f"(top/bottom lists require >= {MIN_REPORT_VOL} articles -- "
          f"{len(eligible)}/{len(rollups)} tickers qualify)")

    def _table(title, rows, value_key):
        print(f"\n[ {title} ]")
        if not rows:
            print("  (none)")
            return
        print(f"  {'TICKER':<8}{'VALUE':>9}{'VOL':>6}{'BULL':>6}{'BEAR':>6}  TOP HEADLINE")
        for r in rows:
            top = (r["top_3_bullish"] or r["top_3_bearish"] or [{}])[0]
            head = (top.get("headline") or "")[:42]
            print(f"  {r['ticker']:<8}{r[value_key]:>+9.3f}{r['sentiment_volume']:>6}"
                  f"{r['bullish_article_count']:>6}{r['bearish_article_count']:>6}  {head}")

    bull = sorted(eligible, key=lambda r: r["weighted_sentiment"], reverse=True)[:10]
    bear = sorted(eligible, key=lambda r: r["weighted_sentiment"])[:10]
    swing = sorted(eligible, key=lambda r: abs(r["sentiment_velocity"]),
                   reverse=True)[:10]

    _table("TOP 10 MOST BULLISH -- by weighted_sentiment", bull, "weighted_sentiment")
    _table("TOP 10 MOST BEARISH -- by weighted_sentiment", bear, "weighted_sentiment")
    _table("TOP 10 BIGGEST MOOD SWINGS -- by |sentiment_velocity|", swing,
           "sentiment_velocity")
    print(bar + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday") -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    if run_type not in ("premarket", "midday", "close"):
        log.warning("Unknown run_type '%s' -- defaulting to 'midday'", run_type)
        run_type = "midday"

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import SUPABASE_SERVICE_KEY, SUPABASE_URL
    from supabase import create_client

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    now = datetime.now(timezone.utc)
    log.info("Sentiment Scorer agent -- backend=%s, run_type=%s", get_backend(), run_type)

    log.info("STAGE 1 -- scoring unscored text sources")
    _score_news(db)
    _score_debates(db)
    _score_filings(db)

    log.info("STAGE 2 -- per-ticker rollup")
    rollups = _build_rollups(db, run_type, now)

    log.info("STAGE 3 -- report")
    _report(rollups)


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
