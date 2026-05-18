"""
Sentiment Scorer
Financial sentiment scoring with two backends:

  - finbert : ProsusAI/finbert via transformers (used when torch + transformers
              are installed and the model loads successfully).
  - lexicon : a finance bull/bear word list -- always available, no heavy deps.

The backend is chosen once, lazily, on first use. If FinBERT cannot load the
scorer transparently falls back to the lexicon so callers never crash.

Public API
----------
    score_text(text)           -> float in [-1.0, +1.0]
    score_texts(iterable)      -> float in [-1.0, +1.0]  (mean over non-empty)
    get_backend()              -> "finbert" | "lexicon"

Convention: positive = bullish, negative = bearish, 0.0 = neutral / unknown.
"""

import logging
import re

log = logging.getLogger(__name__)

# ── Finance lexicon (fallback backend) ─────────────────────────────────────────
_BULL_WORDS = {
    "beat", "beats", "bullish", "buy", "buying", "upgrade", "upgraded",
    "outperform", "breakout", "surge", "surged", "rally", "rallied", "gain",
    "gains", "gained", "growth", "strong", "stronger", "soar", "soared",
    "jump", "jumped", "record", "profit", "profitable", "momentum",
    "undervalued", "long", "calls", "moon", "support", "rebound", "raise",
    "raised", "exceed", "exceeded", "optimistic", "accelerate", "expansion",
    "win", "wins", "winning", "positive", "boom", "uptrend", "green",
    "topped", "rocket", "squeeze", "outperforming", "beats", "upside",
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


class _FinBertBackend:
    name = "finbert"

    def __init__(self):
        from transformers import pipeline  # raises if transformers/torch absent
        self._pipe = pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            truncation=True,
            max_length=512,
        )

    def score(self, text: str) -> float:
        if not text or not text.strip():
            return 0.0
        out = self._pipe(text[:2000])[0]
        label = str(out["label"]).lower()
        conf = float(out["score"])
        if label == "positive":
            return conf
        if label == "negative":
            return -conf
        return 0.0


_backend = None


def _get_backend():
    global _backend
    if _backend is None:
        try:
            _backend = _FinBertBackend()
            log.info("sentiment_scorer: FinBERT backend ready")
        except Exception as exc:
            _backend = _LexiconBackend()
            log.info(
                "sentiment_scorer: FinBERT unavailable (%s) -- using lexicon backend",
                type(exc).__name__,
            )
    return _backend


# ── Public API ─────────────────────────────────────────────────────────────────

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


def get_backend() -> str:
    """Return the active backend name: 'finbert' or 'lexicon'."""
    return _get_backend().name
