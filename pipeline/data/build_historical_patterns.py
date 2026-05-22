"""
Historical Patterns Backfill  (one-time job)
Builds the local historical-analog store: for every ticker, for every trading
day in the look-back window, it computes a 10-feature fingerprint, writes a
textual description, embeds it (all-MiniLM-L6-v2, 384-dim), computes the
forward 1/5/20/60-day returns, and appends everything to:

    pipeline/data/historical_patterns.faiss     -- FAISS IndexFlatIP (cosine)
    pipeline/data/historical_patterns.parquet   -- row-aligned metadata

A LOCAL FAISS index is used instead of Supabase/pgvector: a full S&P-1500 x
5-year store is ~1.9M rows of 384-dim vectors (~3 GB), far beyond Supabase's
free-tier storage cap. FAISS row i corresponds exactly to parquet row i.

Embeddings are L2-normalized, so the IndexFlatIP inner product IS cosine
similarity. The index is exact (brute-force) -- fine for ~40 queries/day.

SCALE
  A full run is ~1.9M rows and takes roughly 4-6 hours (CPU embedding is the
  bottleneck). Run once, overnight. Flags scale it down for testing:
      --limit N    only the N most liquid tickers   (default: all ~1500)
      --years Y    look-back window in years         (default: 5)
      --sample S   keep every S-th trading day       (default: 1 = all)

Usage:
    python pipeline/data/build_historical_patterns.py [--limit N] [--years Y] [--sample S]
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import faiss
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import TICKER_UNIVERSE_PATH  # noqa: E402
from processors.fingerprint import (  # noqa: E402
    EMBED_DIM, FEATURE_KEYS, PATTERNS_INDEX_PATH, PATTERNS_META_PATH,
    build_feature_frame, embed, fingerprint_text, forward_returns, label_from_5d,
)

DOWNLOAD_BATCH = 150     # tickers per yfinance.download() call

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Universe & benchmarks
# ══════════════════════════════════════════════════════════════════════════════

def _rank_by_liquidity(tickers: list[str], limit: int) -> list[str]:
    """Top-`limit` tickers by 20-day average dollar volume."""
    if len(tickers) <= limit:
        return tickers
    dollar_vol: dict[str, float] = {}
    for i in range(0, len(tickers), DOWNLOAD_BATCH):
        batch = tickers[i : i + DOWNLOAD_BATCH]
        try:
            raw = yf.download(batch, period="1mo", interval="1d",
                              auto_adjust=True, progress=False, group_by="ticker")
        except Exception:
            continue
        if raw is None or raw.empty:
            continue
        for t in batch:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                dv = (df["Close"] * df["Volume"]).dropna()
                if not dv.empty:
                    dollar_vol[t] = float(dv.tail(20).mean())
            except (KeyError, TypeError):
                pass
    ranked = sorted(dollar_vol, key=dollar_vol.get, reverse=True)[:limit]
    log.info("Ranked universe down to top %d by liquidity", len(ranked))
    return ranked


def _benchmark_series(period: str):
    out = {}
    for sym, key in (("SPY", "spy"), ("^VIX", "vix"), ("^TNX", "tnx")):
        try:
            df = yf.download(sym, period=period, interval="1d",
                             auto_adjust=True, progress=False)
            s = df["Close"].dropna()
            out[key] = s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s
        except Exception as exc:
            log.warning("benchmark %s failed: %s", sym, exc)
            out[key] = pd.Series(dtype=float)
    return out["spy"], out["vix"], out["tnx"]


# ══════════════════════════════════════════════════════════════════════════════
# Per-ticker row construction
# ══════════════════════════════════════════════════════════════════════════════

def _ticker_rows(ticker: str, df: pd.DataFrame, spy, vix, tnx,
                 sample: int) -> list[dict]:
    """Build fingerprint rows (without embeddings) for one ticker."""
    if df is None or len(df) < 80:
        return []
    df = df.rename(columns=str.lower)
    if not {"open", "high", "low", "close", "volume"}.issubset(df.columns):
        return []

    feats = build_feature_frame(df, spy, vix, tnx)
    fwd   = forward_returns(df["close"])
    rows: list[dict] = []

    for n, (idx, frow) in enumerate(feats.iterrows()):
        if sample > 1 and n % sample != 0:
            continue
        fdict = {k: frow.get(k) for k in FEATURE_KEYS}
        if any(pd.isna(v) for v in fdict.values()):
            continue                                  # need a full fingerprint
        fdict = {k: round(float(v), 4) for k, v in fdict.items()}

        fr = fwd.loc[idx]

        def _r(col):
            v = fr.get(col)
            return None if pd.isna(v) else round(float(v), 4)

        r5 = _r("fwd_5")
        rows.append({
            "ticker":               ticker,
            "date":                 idx.date().isoformat(),
            "fingerprint_features": json.dumps(fdict),
            "fingerprint_text":     fingerprint_text(fdict),
            "forward_return_1d":    _r("fwd_1"),
            "forward_return_5d":    r5,
            "forward_return_20d":   _r("fwd_20"),
            "forward_return_60d":   _r("fwd_60"),
            "forward_return_label": label_from_5d(r5),
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(limit: int | None, years: int, sample: int) -> None:
    universe = (
        pd.read_csv(TICKER_UNIVERSE_PATH)["ticker"].dropna().astype(str).tolist()
    )
    tickers = _rank_by_liquidity(universe, limit) if limit else universe
    est_rows = len(tickers) * (252 * years) // max(sample, 1)
    log.info("Backfill: %d tickers x %d years, sample=1/%d -- ~%s rows estimated",
             len(tickers), years, sample, f"{est_rows:,}")

    period = f"{years + 1}y"            # +1y so rolling windows are warm
    spy, vix, tnx = _benchmark_series(period)
    log.info("Benchmarks loaded -- SPY %d / VIX %d / TNX %d bars",
             len(spy), len(vix), len(tnx))

    PATTERNS_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    index = faiss.IndexFlatIP(EMBED_DIM)        # inner product == cosine (normalized)
    writer: pq.ParquetWriter | None = None
    total = 0
    started = time.time()
    n_batches = (len(tickers) + DOWNLOAD_BATCH - 1) // DOWNLOAD_BATCH

    for bnum, i in enumerate(range(0, len(tickers), DOWNLOAD_BATCH), start=1):
        batch = tickers[i : i + DOWNLOAD_BATCH]
        try:
            raw = yf.download(batch, period=period, interval="1d",
                              auto_adjust=True, progress=False, group_by="ticker")
        except Exception as exc:
            log.warning("download batch %d/%d failed: %s", bnum, n_batches, exc)
            continue
        if raw is None or raw.empty:
            continue
        multi = isinstance(raw.columns, pd.MultiIndex)

        rows: list[dict] = []
        for t in batch:
            try:
                df = (raw[t] if multi else raw).dropna(how="all")
            except KeyError:
                continue
            rows.extend(_ticker_rows(t, df, spy, vix, tnx, sample))

        if not rows:
            log.info("batch %d/%d -- no rows", bnum, n_batches)
            continue

        # Embed, add to the FAISS index, append metadata -- order kept aligned.
        vecs = np.asarray(embed([r["fingerprint_text"] for r in rows]),
                          dtype="float32")
        index.add(vecs)

        table = pa.Table.from_pylist(rows)
        if writer is None:
            writer = pq.ParquetWriter(str(PATTERNS_META_PATH), table.schema)
        writer.write_table(table)

        total += len(rows)
        rate = total / max(time.time() - started, 1)
        log.info("batch %d/%d -- +%d rows (%s total, %.0f rows/s)",
                 bnum, n_batches, len(rows), f"{total:,}", rate)

    if writer is not None:
        writer.close()
    faiss.write_index(index, str(PATTERNS_INDEX_PATH))

    log.info("Backfill complete. %s fingerprints indexed.", f"{total:,}")
    log.info("  FAISS index : %s", PATTERNS_INDEX_PATH)
    log.info("  metadata    : %s", PATTERNS_META_PATH)
    if total == 0:
        log.warning("No rows produced -- check ticker universe / network.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Historical patterns backfill (FAISS)")
    ap.add_argument("--limit", type=int, default=None,
                    help="scan only the N most liquid tickers (default: all)")
    ap.add_argument("--years", type=int, default=5, help="look-back years")
    ap.add_argument("--sample", type=int, default=1,
                    help="keep every S-th trading day (default: 1)")
    args = ap.parse_args()
    run(args.limit, args.years, max(args.sample, 1))
