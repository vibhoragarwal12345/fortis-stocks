"""
Ranking Engine
Blends market, news and crowd-intelligence signals into a composite score and
writes the day's top-ranked focus list to the ranked_focus_list table.

Signal sources:
  - market_snapshots : relative_volume + |gap_pct|        (market momentum)
  - news_items       : recent article volume per ticker   (news flow)
  - ticker_debates   : the PRIMARY social signal source (replaces the old
                       social_mentions mention-count input):
                         * attention_score       -> crowd-attention component
                         * sentiment_balance      -> directional conviction
                         * retail_pro_divergence  -> divergence bonus, since a
                           retail/professional split often precedes volatility

Composite (0..100) = 100 * (
    0.30 * market_score     +
    0.18 * news_score       +
    0.37 * crowd_attention  +
    0.15 * divergence_bonus )

Usage:
    python pipeline/agents/ranking_engine.py [premarket|midday|close]
"""

import json
import logging
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from supabase import create_client

# ── Path setup ─────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL

# ── Settings ───────────────────────────────────────────────────────────────────
TOP_N                  = 50
SNAPSHOT_LOOKBACK_DAYS = 4
NEWS_WINDOW_HOURS      = 48
PAGE                   = 1000
VALID_RUN_TYPES        = ("premarket", "midday", "close")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Data loaders
# ══════════════════════════════════════════════════════════════════════════════

def _latest_snapshots(db) -> dict[str, dict]:
    """Most recent market_snapshots row per ticker (last few days)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_LOOKBACK_DAYS)).isoformat()
    latest: dict[str, dict] = {}
    start = 0
    while True:
        resp = (
            db.table("market_snapshots")
            .select("ticker,price,gap_pct,relative_volume,volume,snapshot_time")
            .gte("snapshot_time", cutoff)
            .order("snapshot_time", desc=True)
            .range(start, start + PAGE - 1)
            .execute()
        )
        batch = resp.data or []
        for r in batch:
            t = r.get("ticker")
            if t and t not in latest:        # desc order -> first seen is newest
                latest[t] = r
        if len(batch) < PAGE:
            break
        start += PAGE
    log.info("Loaded latest snapshots for %d tickers", len(latest))
    return latest


def _news_counts(db) -> Counter:
    """Recent news-article count per ticker."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=NEWS_WINDOW_HOURS)).isoformat()
    counts: Counter = Counter()
    start = 0
    while True:
        resp = (
            db.table("news_items")
            .select("ticker")
            .gte("published_at", cutoff)
            .range(start, start + PAGE - 1)
            .execute()
        )
        batch = resp.data or []
        for r in batch:
            if r.get("ticker"):
                counts[r["ticker"]] += 1
        if len(batch) < PAGE:
            break
        start += PAGE
    log.info("Loaded news volume for %d tickers", len(counts))
    return counts


def _debates(db, today: str, run_type: str) -> dict[str, dict]:
    try:
        resp = (
            db.table("ticker_debates").select("*")
            .eq("snapshot_date", today).eq("run_type", run_type).execute()
        )
        rows = {r["ticker"]: r for r in (resp.data or [])}
        log.info("Loaded %d ticker_debates rows for %s/%s", len(rows), today, run_type)
        return rows
    except Exception as exc:
        log.warning("ticker_debates query failed (%s) -- ranking without social signal", exc)
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# Scoring
# ══════════════════════════════════════════════════════════════════════════════

def _divergence_mag(debate: dict) -> float:
    raw = debate.get("raw_signals") or {}
    try:
        return min(abs(float(raw["scores"]["divergence"])) / 100.0, 1.0)
    except (KeyError, TypeError, ValueError):
        return 0.0


def _compute(snapshots: dict, news: Counter, debates: dict) -> list[dict]:
    ranked: list[dict] = []
    for t, snap in snapshots.items():
        rv  = float(snap.get("relative_volume") or 0.0)
        gap = float(snap.get("gap_pct") or 0.0)
        market_score = 0.5 * min(rv / 3.0, 1.0) + 0.5 * min(abs(gap) / 8.0, 1.0)

        news_count = int(news.get(t, 0))
        news_score = min(news_count / 15.0, 1.0)

        d = debates.get(t)
        attention = (float(d["attention_score"] or 0.0) / 100.0) if d else 0.0
        attention = min(max(attention, 0.0), 1.0)
        sentiment = float(d["sentiment_balance"]) if d and d.get("sentiment_balance") is not None else 0.0
        div_bonus = _divergence_mag(d) if d else 0.0

        composite = 100.0 * (
            0.30 * market_score + 0.18 * news_score
            + 0.37 * attention + 0.15 * div_bonus
        )

        thesis_parts = [f"rel.vol {rv:.1f}x", f"gap {gap:+.1f}%"]
        if news_count:
            thesis_parts.append(f"{news_count} news items")
        if d:
            thesis_parts.append(f"crowd attention {float(d['attention_score'] or 0):.0f}/100")
            if abs(sentiment) > 15:
                thesis_parts.append("bulls leading" if sentiment > 0 else "bears leading")
            if div_bonus > 0.4:
                thesis_parts.append("notable retail/pro divergence")

        ranked.append({
            "ticker": t,
            "composite_score": round(composite, 2),
            "thesis": f"{t}: " + ", ".join(thesis_parts) + ".",
            "signals": {
                "market_score":      round(market_score, 4),
                "news_score":        round(news_score, 4),
                "crowd_attention":   round(attention, 4),
                "divergence_bonus":  round(div_bonus, 4),
                "relative_volume":   round(rv, 4),
                "gap_pct":           round(gap, 4),
                "news_count":        news_count,
                "attention_score":   float(d["attention_score"]) if d and d.get("attention_score") is not None else None,
                "sentiment_balance": sentiment if d else None,
            },
        })

    ranked.sort(key=lambda r: r["composite_score"], reverse=True)
    return ranked[:TOP_N]


# ══════════════════════════════════════════════════════════════════════════════
# Persistence
# ══════════════════════════════════════════════════════════════════════════════

def _write_focus_list(db, run_date: str, run_type: str, ranked: list[dict]) -> int:
    # Idempotent: clear any prior rows for this run_date + run_type first.
    try:
        db.table("ranked_focus_list").delete().eq("run_date", run_date).eq("run_type", run_type).execute()
    except Exception as exc:
        log.warning("Could not clear prior %s/%s rows: %s", run_date, run_type, exc)

    rows = [{
        "run_date":        run_date,
        "run_type":        run_type,
        "ticker":          r["ticker"],
        "rank":            i,
        "composite_score": r["composite_score"],
        "thesis":          r["thesis"],
        "signals":         r["signals"],
    } for i, r in enumerate(ranked, start=1)]

    if not rows:
        return 0
    try:
        resp = db.table("ranked_focus_list").insert(rows).execute()
        return len(resp.data) if resp.data else 0
    except Exception as exc:
        log.error("ranked_focus_list insert failed: %s", exc)
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday") -> None:
    if run_type not in VALID_RUN_TYPES:
        log.warning("Unknown run_type '%s' -- defaulting to 'midday'", run_type)
        run_type = "midday"

    today = datetime.now(timezone.utc).date().isoformat()
    log.info("Ranking run: date=%s  type=%s", today, run_type)

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    snapshots = _latest_snapshots(db)
    if not snapshots:
        log.error("No market_snapshots found -- run market_data.py first. Aborting.")
        return
    news    = _news_counts(db)
    debates = _debates(db, today, run_type)

    ranked = _compute(snapshots, news, debates)
    inserted = _write_focus_list(db, today, run_type, ranked)
    log.info("Wrote %d rows to ranked_focus_list (%s/%s)", inserted, today, run_type)

    log.info("")
    log.info("== Top 15 ranked focus list ==")
    log.info("%-5s  %-8s  %-10s  %s", "RANK", "TICKER", "SCORE", "THESIS")
    for i, r in enumerate(ranked[:15], start=1):
        log.info("%-5d  %-8s  %-10.2f  %s", i, r["ticker"], r["composite_score"], r["thesis"])


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
