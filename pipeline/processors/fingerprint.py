"""
Fingerprint -- shared feature/embedding logic for historical-analog detection.

Used by both:
  - pipeline/data/build_historical_patterns.py  (5-year backfill)
  - pipeline/agents/pattern_matcher.py           (daily matching)

A daily "fingerprint" is 10 numeric features describing a ticker's state on a
given day. The features are turned into a short textual description and
embedded with sentence-transformers/all-MiniLM-L6-v2 (384-dim) so that
similar market setups land near each other in vector space.

The 10 features:
  rsi              RSI(14)
  macd_hist        MACD(12,26,9) histogram
  pct_b            Bollinger %B (position within the 20,2 bands)
  rel_vol          volume / 30-day average volume
  dist_52w_high    % distance from the 252-day high (<=0 at the high)
  ret_20d          20-day price return (%)
  ret_60d          60-day price return (%)
  rel_strength     20-day return minus SPY's 20-day return (%) -- a
                   market-relative proxy; tickers.csv carries no GICS
                   sector, so a true sector-relative figure is not available
  vix              VIX close that day
  tnx              10-year Treasury yield that day
"""

from pathlib import Path

import pandas as pd
import pandas_ta as ta  # noqa: F401  -- registers the df.ta accessor

FEATURE_KEYS = ("rsi", "macd_hist", "pct_b", "rel_vol", "dist_52w_high",
                "ret_20d", "ret_60d", "rel_strength", "vix", "tnx")

EMBED_MODEL = "all-MiniLM-L6-v2"
EMBED_DIM = 384

# Local FAISS analog store (chosen over pgvector to avoid Supabase's storage
# cap). The backfill writes both files; the matcher reads them.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PATTERNS_INDEX_PATH = _DATA_DIR / "historical_patterns.faiss"     # FAISS vectors
PATTERNS_META_PATH  = _DATA_DIR / "historical_patterns.parquet"   # aligned metadata


# ══════════════════════════════════════════════════════════════════════════════
# Feature engineering
# ══════════════════════════════════════════════════════════════════════════════

def _col(df: pd.DataFrame, prefix: str):
    for c in df.columns:
        if c.startswith(prefix):
            return df[c]
    return None


def build_feature_frame(df: pd.DataFrame, spy_close: pd.Series,
                        vix_close: pd.Series, tnx_close: pd.Series) -> pd.DataFrame:
    """Per-day feature DataFrame for one ticker (lowercase OHLCV, DatetimeIndex)."""
    close, volume = df["close"], df["volume"]

    macd = df.ta.macd()
    bb = df.ta.bbands(length=20, std=2)

    feat = pd.DataFrame(index=df.index)
    feat["rsi"]           = df.ta.rsi(length=14)
    feat["macd_hist"]     = _col(macd, "MACDh") if macd is not None else None
    feat["pct_b"]         = _col(bb, "BBP") if bb is not None else None
    feat["rel_vol"]       = volume / volume.rolling(30, min_periods=10).mean()
    feat["dist_52w_high"] = (close / close.rolling(252, min_periods=60).max() - 1) * 100
    feat["ret_20d"]       = close.pct_change(20) * 100
    feat["ret_60d"]       = close.pct_change(60) * 100

    spy = spy_close.reindex(df.index, method="ffill")
    feat["rel_strength"]  = feat["ret_20d"] - (spy.pct_change(20) * 100)
    feat["vix"]           = vix_close.reindex(df.index, method="ffill")
    feat["tnx"]           = tnx_close.reindex(df.index, method="ffill")
    return feat


def forward_returns(close: pd.Series) -> pd.DataFrame:
    """Forward 1/5/20/60-day returns (%), NaN where the future isn't available."""
    out = pd.DataFrame(index=close.index)
    for n in (1, 5, 20, 60):
        out[f"fwd_{n}"] = (close.shift(-n) / close - 1) * 100
    return out


def label_from_5d(ret_5d: float | None) -> str:
    """Bucket a 5-day forward return into a categorical label."""
    if ret_5d is None or pd.isna(ret_5d):
        return "unknown"
    if ret_5d > 5:
        return "big_win"
    if ret_5d > 1.5:
        return "win"
    if ret_5d < -5:
        return "big_loss"
    if ret_5d < -1.5:
        return "loss"
    return "neutral"


# ══════════════════════════════════════════════════════════════════════════════
# Text description
# ══════════════════════════════════════════════════════════════════════════════

def fingerprint_text(f: dict) -> str:
    """A short natural-language description of a day's fingerprint -- embedded."""
    rsi = f.get("rsi") or 50.0
    rsi_d = "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral RSI"
    macd_d = "MACD bullish" if (f.get("macd_hist") or 0) > 0 else "MACD bearish"

    pb = f.get("pct_b")
    if pb is None:
        pb_d = "mid Bollinger band"
    elif pb > 1:
        pb_d = "above upper Bollinger band"
    elif pb < 0:
        pb_d = "below lower Bollinger band"
    elif pb > 0.5:
        pb_d = "upper Bollinger half"
    else:
        pb_d = "lower Bollinger half"

    rv = f.get("rel_vol") or 1.0
    dh = f.get("dist_52w_high") or 0.0
    dh_d = ("at 52-week high" if dh > -2
            else f"{abs(dh):.0f}% below 52-week high")

    r20, r60 = f.get("ret_20d") or 0.0, f.get("ret_60d") or 0.0
    trend = ("uptrend" if r20 > 0 and r60 > 0
             else "downtrend" if r20 < 0 and r60 < 0 else "mixed trend")

    rs = f.get("rel_strength") or 0.0
    rs_d = ("leading the market" if rs > 2
            else "lagging the market" if rs < -2 else "in line with the market")

    vix = f.get("vix") or 0.0
    tnx = f.get("tnx") or 0.0
    return (f"RSI {rsi:.0f} {rsi_d}, {macd_d}, {pb_d}, {rv:.1f}x volume, "
            f"{dh_d}, 20-day return {r20:+.1f}%, 60-day return {r60:+.1f}%, "
            f"{rs_d}, {trend}, VIX {vix:.0f}, 10Y yield {tnx:.1f}")


# ══════════════════════════════════════════════════════════════════════════════
# Embedding
# ══════════════════════════════════════════════════════════════════════════════

_embedder = None


def get_embedder():
    """Lazily load the sentence-transformers model (cached for the process)."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(EMBED_MODEL)
    return _embedder


def embed(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """Embed a list of texts -> list of 384-float vectors."""
    if not texts:
        return []
    vecs = get_embedder().encode(texts, batch_size=batch_size,
                                 show_progress_bar=False, normalize_embeddings=True)
    return [[round(float(x), 6) for x in v] for v in vecs]
