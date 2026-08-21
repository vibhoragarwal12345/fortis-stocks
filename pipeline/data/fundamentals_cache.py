"""
Full-universe fundamentals cache.
=================================

Warms and serves the `fundamentals` table (migration 057) that the v2 factor
ranker reads. Two responsibilities:

  refresh(...)   Scheduled job. Walks the ~3,300-name universe, finds tickers
                 whose cached fundamentals are MISSING or STALE (older than
                 --max-age-days), fetches a rate-limited, budget-capped batch
                 from Finnhub (with a yfinance fallback -- see factor_engine),
                 and upserts them to Supabase. A budget of ~470/day keeps the
                 whole universe inside the free tier on a weekly cycle; a
                 --budget of 0 means "no cap" (a full cold-start warm).

  load_all(db)   Read path for the scan. One paginated SELECT -> {ticker: {...}}
                 so the factor ranker needs ZERO API calls at scan time. Cold or
                 stale names simply aren't in the dict; the ranker treats a
                 missing name as ineligible (fundamentals must gate) and its
                 built-in fallback keeps the scan producing a board while the
                 cache warms.

The scan disk cache (_finnhub_cache) is deliberately NOT relied on in
production -- Actions runners are ephemeral. Supabase is the durable store.

CLI
    python -m pipeline.data.fundamentals_cache refresh --budget 470
    python -m pipeline.data.fundamentals_cache refresh --budget 0 --max-age-days 7
    python -m pipeline.data.fundamentals_cache status
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402
from scan.factor_engine import fetch_fundamentals  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

UNIVERSE_CSV = ROOT / "data" / "full_universe.csv"
_FIELDS = ("sector", "market_cap_usd", "gross_margin", "oper_margin",
           "net_margin", "roe", "roa", "debt_equity", "current_ratio",
           "rev_growth", "pe", "pfcf_share", "ps", "beta", "avg_vol_10d_m")


def _db():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _universe_tickers() -> list[str]:
    df = pd.read_csv(UNIVERSE_CSV)
    return [str(t).strip().upper() for t in df["ticker"].dropna().tolist()]


def _cached_state(db) -> dict[str, str]:
    """{ticker: fetched_at_iso} for everything already cached."""
    out: dict[str, str] = {}
    start, page = 0, 1000
    while True:
        rows = (db.table("fundamentals").select("ticker,fetched_at")
                  .range(start, start + page - 1).execute().data or [])
        for r in rows:
            out[r["ticker"]] = r.get("fetched_at") or ""
        if len(rows) < page:
            break
        start += page
    return out


def _row_from_fetch(ticker: str, f: dict) -> dict:
    """Map factor_engine.fetch_fundamentals() output onto the table columns."""
    mktcap = f.get("mkt_cap_usd")
    src = "finnhub+yfinance" if f.get("gross_margin") is not None else "finnhub"
    return {
        "ticker":        ticker,
        "sector":        f.get("sector"),
        "market_cap_usd": mktcap if mktcap else None,
        "gross_margin":  f.get("gross_margin"),
        "oper_margin":   f.get("oper_margin"),
        "net_margin":    f.get("net_margin"),
        "roe":           f.get("roe"),
        "roa":           f.get("roa"),
        "debt_equity":   f.get("debt_equity"),
        "current_ratio": f.get("current_ratio"),
        "rev_growth":    f.get("rev_growth"),
        "pe":            f.get("pe"),
        "pfcf_share":    f.get("pfcf_share"),
        "ps":            f.get("ps"),
        "beta":          f.get("beta"),
        "avg_vol_10d_m": f.get("avg_vol_10d_m"),
        "source":        src,
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
        "updated_at":    datetime.now(timezone.utc).isoformat(),
    }


def refresh(budget: int = 470, max_age_days: int = 7,
            sleep_sec: float = 0.4) -> int:
    """Fetch fundamentals for missing/stale universe tickers, oldest-first,
    up to `budget` (0 = no cap). Returns the number upserted."""
    db = _db()
    universe = _universe_tickers()
    cached = _cached_state(db)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

    # Priority: never-cached first, then stalest.
    missing = [t for t in universe if t not in cached]
    stale = sorted([t for t in universe if t in cached and (cached[t] or "") < cutoff],
                   key=lambda t: cached[t] or "")
    todo = missing + stale
    if budget and budget > 0:
        todo = todo[:budget]

    log.info("universe=%d  cached=%d  missing=%d  stale=%d  -> refreshing %d "
             "(budget=%s, max_age=%dd)", len(universe), len(cached),
             len(missing), len(stale), len(todo), budget or "none", max_age_days)

    upserted, failed = 0, 0
    batch: list[dict] = []
    for i, t in enumerate(todo, 1):
        try:
            f = fetch_fundamentals(t)          # Finnhub (+yf fallback), disk-cached
            batch.append(_row_from_fetch(t, f))
        except Exception as exc:               # noqa: BLE001
            failed += 1
            log.debug("fetch failed %s: %s", t, exc)
        # flush in chunks
        if len(batch) >= 100:
            db.table("fundamentals").upsert(batch, on_conflict="ticker").execute()
            upserted += len(batch); batch = []
            log.info("  %d/%d upserted=%d failed=%d", i, len(todo), upserted, failed)
        time.sleep(sleep_sec)                   # gentle on the free tier
    if batch:
        db.table("fundamentals").upsert(batch, on_conflict="ticker").execute()
        upserted += len(batch)
    log.info("refresh done: upserted=%d failed=%d", upserted, failed)
    return upserted


def load_all(db=None) -> dict[str, dict]:
    """Batch-read the whole cache into {ticker: {field: value}}. The scan's
    read path -- zero API calls."""
    db = db or _db()
    out: dict[str, dict] = {}
    start, page = 0, 1000
    cols = "ticker," + ",".join(_FIELDS) + ",fetched_at"
    while True:
        rows = (db.table("fundamentals").select(cols)
                  .range(start, start + page - 1).execute().data or [])
        for r in rows:
            out[r["ticker"]] = r
        if len(rows) < page:
            break
        start += page
    return out


def status() -> None:
    db = _db()
    universe = _universe_tickers()
    cached = _cached_state(db)
    fresh_cut = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    fresh = sum(1 for t in universe if (cached.get(t) or "") >= fresh_cut)
    print(f"universe:        {len(universe)}")
    print(f"cached:          {len(cached)}  ({100*len(cached)/max(len(universe),1):.0f}% of universe)")
    print(f"fresh (<=7d):    {fresh}")
    print(f"missing:         {sum(1 for t in universe if t not in cached)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("refresh")
    r.add_argument("--budget", type=int, default=470,
                   help="max tickers to fetch this run (0 = no cap)")
    r.add_argument("--max-age-days", type=int, default=7)
    sub.add_parser("status")
    args = ap.parse_args()
    if args.cmd == "refresh":
        refresh(budget=args.budget, max_age_days=args.max_age_days)
    elif args.cmd == "status":
        status()
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
