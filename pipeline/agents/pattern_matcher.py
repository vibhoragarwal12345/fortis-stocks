"""
Pattern Matcher
Historical-analog detection. For each high-attention ticker today it computes
today's 10-feature fingerprint, embeds it, and uses a local FAISS index to
find the 10 most similar days in market history -- then reports what happened
next ("past instances of this setup returned +4.2% over 5 days, 7/10 positive").

Depends on the local analog store built by the one-time backfill:
    pipeline/data/historical_patterns.faiss     (FAISS IndexFlatIP, cosine)
    pipeline/data/historical_patterns.parquet   (row-aligned metadata)
Run pipeline/data/build_historical_patterns.py first if they are missing.

The daily pattern_matches output is still persisted to Supabase (small table,
migration 008). High-attention candidates come from ticker_debates
(attention_score); if that table is empty the matcher falls back to the day's
highest-conviction options_signals names, or a CLI ticker list.

Usage:
    python pipeline/agents/pattern_matcher.py [TICKER ...]
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import faiss
import numpy as np
import pandas as pd
import yfinance as yf
from supabase import create_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from processors.fingerprint import (  # noqa: E402
    FEATURE_KEYS, PATTERNS_INDEX_PATH, PATTERNS_META_PATH,
    build_feature_frame, embed, fingerprint_text,
)

ATTENTION_THRESHOLD = 60      # ticker_debates.attention_score floor
MAX_CANDIDATES      = 40      # cap on tickers matched per run
NEIGHBORS           = 10      # historical analogs kept per ticker
SEARCH_FANOUT       = 4       # search NEIGHBORS*fanout, then drop no-forward rows

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Candidate selection
# ══════════════════════════════════════════════════════════════════════════════

def _high_attention_tickers(db) -> list[str]:
    """High-attention tickers from ticker_debates, with sensible fallbacks."""
    try:
        rows = (db.table("ticker_debates")
                .select("ticker,attention_score")
                .gte("attention_score", ATTENTION_THRESHOLD)
                .order("attention_score", desc=True).limit(MAX_CANDIDATES)
                .execute().data or [])
        if rows:
            log.info("Candidates: %d high-attention tickers from ticker_debates",
                     len(rows))
            return list(dict.fromkeys(r["ticker"] for r in rows))
    except Exception as exc:
        log.warning("ticker_debates lookup failed: %s", exc)

    log.info("ticker_debates empty -- falling back to top options conviction")
    try:
        rows = (db.table("options_signals")
                .select("ticker,conviction_score")
                .order("conviction_score", desc=True).limit(MAX_CANDIDATES * 3)
                .execute().data or [])
        seen = list(dict.fromkeys(r["ticker"] for r in rows))
        if seen:
            return seen[:MAX_CANDIDATES]
    except Exception as exc:
        log.warning("options_signals fallback failed: %s", exc)
    return []


# ══════════════════════════════════════════════════════════════════════════════
# Today's fingerprint
# ══════════════════════════════════════════════════════════════════════════════

def _todays_fingerprints(tickers: list[str]) -> dict[str, dict]:
    """{ticker: feature dict} for the most recent trading day."""
    bench = {}
    for sym, key in (("SPY", "spy"), ("^VIX", "vix"), ("^TNX", "tnx")):
        try:
            s = yf.download(sym, period="2y", interval="1d",
                            auto_adjust=True, progress=False)["Close"].dropna()
            bench[key] = s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s
        except Exception:
            bench[key] = pd.Series(dtype=float)

    out: dict[str, dict] = {}
    for i in range(0, len(tickers), 100):
        batch = tickers[i : i + 100]
        try:
            raw = yf.download(batch, period="2y", interval="1d",
                              auto_adjust=True, progress=False, group_by="ticker")
        except Exception as exc:
            log.warning("download failed for batch: %s", exc)
            continue
        if raw is None or raw.empty:
            continue
        multi = isinstance(raw.columns, pd.MultiIndex)
        for t in batch:
            try:
                df = (raw[t] if multi else raw).dropna(how="all").rename(columns=str.lower)
                if len(df) < 80:
                    continue
                feats = build_feature_frame(df, bench["spy"], bench["vix"], bench["tnx"])
                last = feats.iloc[-1]
                fdict = {k: last.get(k) for k in FEATURE_KEYS}
                if any(pd.isna(v) for v in fdict.values()):
                    continue
                out[t] = {k: round(float(v), 4) for k, v in fdict.items()}
            except (KeyError, IndexError, TypeError):
                continue
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Analog store
# ══════════════════════════════════════════════════════════════════════════════

def _load_store():
    """Load the FAISS index (mmap) and aligned metadata, or (None, None)."""
    if not PATTERNS_INDEX_PATH.exists() or not PATTERNS_META_PATH.exists():
        return None, None
    index = faiss.read_index(str(PATTERNS_INDEX_PATH), faiss.IO_FLAG_MMAP)
    meta = pd.read_parquet(PATTERNS_META_PATH)
    log.info("Analog store loaded -- %s vectors, %s metadata rows",
             f"{index.ntotal:,}", f"{len(meta):,}")
    return index, meta


def _match(ticker: str, features: dict, index, meta: pd.DataFrame) -> dict | None:
    """Find the NEIGHBORS closest historical days and aggregate their outcomes."""
    text = fingerprint_text(features)
    vec = np.asarray(embed([text]), dtype="float32")           # (1, 384), normalized
    k = min(NEIGHBORS * SEARCH_FANOUT, index.ntotal)
    sims, idxs = index.search(vec, k)

    rows = []
    for sim, ridx in zip(sims[0], idxs[0]):
        if ridx < 0:
            continue
        m = meta.iloc[int(ridx)]
        if pd.isna(m["forward_return_5d"]):          # need a known outcome
            continue
        rows.append((float(sim), m))
        if len(rows) >= NEIGHBORS:
            break
    if not rows:
        return None

    def _avg(col):
        vals = [float(m[col]) for _, m in rows if pd.notna(m[col])]
        return round(sum(vals) / len(vals), 3) if vals else None

    def _winrate(col):
        vals = [float(m[col]) for _, m in rows if pd.notna(m[col])]
        return round(sum(1 for v in vals if v > 0) / len(vals), 3) if vals else None

    confidence = round(sum(s for s, _ in rows) / len(rows) * 100, 1)
    r5 = _avg("forward_return_5d")
    wins5 = sum(1 for _, m in rows if float(m["forward_return_5d"]) > 0)

    sim0, m0 = rows[0]
    analog = (
        f"Closest analog: {m0['ticker']} on {m0['date']} "
        f"(similarity {sim0:.2f}) -- \"{m0['fingerprint_text']}\" -- went "
        f"{float(m0['forward_return_5d']):+.1f}% over the next 5 days. "
        f"Across all {len(rows)} analogs this setup returned {r5:+.1f}% on "
        f"average over 5 days ({wins5}/{len(rows)} positive)."
    )

    return {
        "ticker":             ticker,
        "snapshot_date":      datetime.now(timezone.utc).date().isoformat(),
        "matched_dates":      [{"ticker": m["ticker"], "date": m["date"],
                                "similarity": round(s, 4)} for s, m in rows],
        "avg_1d_return":      _avg("forward_return_1d"),
        "avg_5d_return":      r5,
        "avg_20d_return":     _avg("forward_return_20d"),
        "avg_60d_return":     _avg("forward_return_60d"),
        "win_rate_5d":        _winrate("forward_return_5d"),
        "win_rate_20d":       _winrate("forward_return_20d"),
        "sample_analog_text": analog,
        "confidence":         confidence,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════════

def _report(matches: list[dict]) -> None:
    bar = "=" * 74
    print(f"\n{bar}\nHISTORICAL PATTERN MATCHES -- {len(matches)} tickers\n{bar}")
    ranked = sorted(matches, key=lambda m: (m["avg_5d_return"] or -999), reverse=True)

    print("\n[ 5 EXAMPLE PATTERN MATCHES ]")
    for m in ranked[:5]:
        print(f"\n  {m['ticker']}  (confidence {m['confidence']:.0f}, "
              f"5d analog avg {(m['avg_5d_return'] or 0):+.1f}%)")
        print(f"    {m['sample_analog_text']}")

    print(f"\n[ WIN RATES -- TOP {min(30, len(ranked))} RANKED TICKERS ]")
    print(f"  {'TICKER':<8}{'AVG 5D':>9}{'AVG 20D':>9}{'WIN 5D':>9}"
          f"{'WIN 20D':>9}{'CONF':>7}")
    for m in ranked[:30]:
        print(f"  {m['ticker']:<8}{(m['avg_5d_return'] or 0):>+8.1f}%"
              f"{(m['avg_20d_return'] or 0):>+8.1f}%"
              f"{(m['win_rate_5d'] or 0)*100:>8.0f}%"
              f"{(m['win_rate_20d'] or 0)*100:>8.0f}%"
              f"{m['confidence']:>7.0f}")
    print(bar + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(explicit_tickers: list[str] | None = None) -> None:
    index, meta = _load_store()
    if index is None:
        log.warning("Analog store not found at %s -- run "
                    "pipeline/data/build_historical_patterns.py first.",
                    PATTERNS_INDEX_PATH)
        return

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    tickers = explicit_tickers or _high_attention_tickers(db)
    if not tickers:
        log.warning("No candidate tickers found -- nothing to match.")
        return
    tickers = tickers[:MAX_CANDIDATES]
    log.info("Matching %d tickers against the analog store", len(tickers))

    fingerprints = _todays_fingerprints(tickers)
    log.info("Computed today's fingerprint for %d/%d tickers",
             len(fingerprints), len(tickers))

    matches = []
    for t in tickers:
        if t in fingerprints:
            m = _match(t, fingerprints[t], index, meta)
            if m:
                matches.append(m)
    log.info("Produced %d pattern matches", len(matches))
    if not matches:
        log.warning("No matches produced.")
        return

    inserted = 0
    for i in range(0, len(matches), 100):
        batch = matches[i : i + 100]
        try:
            resp = db.table("pattern_matches").upsert(
                batch, on_conflict="ticker,snapshot_date").execute()
            inserted += len(resp.data) if resp.data else 0
        except Exception as exc:
            log.warning("pattern_matches upsert failed: %s", exc)
    log.info("pattern_matches: %d/%d rows stored", inserted, len(matches))
    if inserted == 0:
        log.warning("Nothing persisted -- apply supabase/migrations/"
                    "008_pattern_matches.sql, then re-run.")

    _report(matches)


if __name__ == "__main__":
    args = [a.upper() for a in sys.argv[1:]] or None
    run(args)
