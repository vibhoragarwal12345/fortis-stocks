"""
Stage 4 -- Emerging Watchlist Manager
=====================================

Maintains the persistent watchlist of multibagger candidates. Operates in
three modes:

    --add        Take the latest theses and upsert qualifying candidates
                 onto the watchlist (tier_1 / tier_2 always; tier_3 only
                 if multibagger_score >= 60).

    --update     Refresh current_price / current_market_cap / return
                 figures for every active watchlist row.

    --graduate   Names with current_market_cap > $10B graduate (status =
                 'graduated') and flow into the daily pipeline universe.

    --archive    Names with status='thesis_broken' get archived (status =
                 'archived').

CLI
    python pipeline/multibagger/watchlist_manager.py --add
    python pipeline/multibagger/watchlist_manager.py --update
    python pipeline/multibagger/watchlist_manager.py --graduate
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import FINNHUB_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

GRADUATION_MARKET_CAP = 10_000_000_000   # $10B -- into the daily pipeline universe
TIER3_MIN_SCORE = 60                      # tier_3 needs >= this score to join watchlist


def _finnhub_quote(ticker: str) -> dict | None:
    if not FINNHUB_API_KEY:
        return None
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": ticker, "token": FINNHUB_API_KEY},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


def _finnhub_profile_market_cap(ticker: str) -> float | None:
    if not FINNHUB_API_KEY:
        return None
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/stock/profile2",
            params={"symbol": ticker, "token": FINNHUB_API_KEY},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        obj = r.json() or {}
        mc = obj.get("marketCapitalization")
        return float(mc) * 1_000_000 if isinstance(mc, (int, float)) else None
    except (requests.RequestException, ValueError):
        return None


def _clean_for_json(v):
    import math
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {k: _clean_for_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean_for_json(x) for x in v]
    return v


# ══════════════════════════════════════════════════════════════════════════════
# Add
# ══════════════════════════════════════════════════════════════════════════════

def add_qualifying(throttle_ms: int = 1100) -> int:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Latest thesis per ticker
    theses = (
        db.table("multibagger_theses")
          .select("*")
          .order("generated_at", desc=True)
          .execute()
          .data or []
    )
    latest_by_ticker: dict[str, dict] = {}
    for t in theses:
        latest_by_ticker.setdefault(t["ticker"], t)

    if not latest_by_ticker:
        log.warning("No theses found -- run deep_research.py first.")
        return 0

    # Existing watchlist (so we don't duplicate)
    existing = {
        r["ticker"]: r for r in (
            db.table("emerging_watchlist").select("ticker, status").execute().data or []
        )
    }

    added = 0
    for ticker, thesis in latest_by_ticker.items():
        tier = thesis.get("conviction_tier") or "tier_3_speculative"
        score = float(thesis.get("multibagger_score") or 0)
        if tier == "tier_3_speculative" and score < TIER3_MIN_SCORE:
            continue
        if ticker in existing and existing[ticker]["status"] in ("active", "thesis_intact", "thesis_weakening"):
            continue
        quote = _finnhub_quote(ticker)
        price = quote.get("c") if isinstance(quote, dict) else None
        market_cap = _finnhub_profile_market_cap(ticker)

        snapshot = thesis.get("data_snapshot") or {}
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except Exception:
                snapshot = {}
        if market_cap is None:
            market_cap = snapshot.get("market_cap_usd")

        payload = _clean_for_json({
            "ticker": ticker,
            "added_date": date.today().isoformat(),
            "conviction_tier": tier,
            "entry_price": price,
            "entry_market_cap": market_cap,
            "thesis_summary": (thesis.get("business_model") or "")[:1500],
            "the_10x_path": thesis.get("the_10x_path"),
            "what_kills_it": thesis.get("what_kills_it"),
            "key_metrics_to_track": thesis.get("key_metrics_to_track") or [],
            "multibagger_score": score,
            "status": "active",
            "current_price": price,
            "current_market_cap": market_cap,
            "return_since_added": 0,
            "thesis_id": thesis.get("id"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            db.table("emerging_watchlist").upsert(payload, on_conflict="tenant_id,ticker").execute()
            added += 1
            log.info("  added %s (%s, score=%.1f)", ticker, tier, score)
        except Exception as exc:
            log.warning("  %s add failed: %s", ticker, exc)
        time.sleep(throttle_ms / 1000.0)
    log.info("Added/refreshed %d watchlist rows.", added)
    return added


# ══════════════════════════════════════════════════════════════════════════════
# Update prices
# ══════════════════════════════════════════════════════════════════════════════

def update_prices(throttle_ms: int = 1100) -> int:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    rows = (
        db.table("emerging_watchlist")
          .select("id, ticker, entry_price, peak_return_pct, max_drawdown_pct")
          .in_("status", ["active", "thesis_intact", "thesis_weakening"])
          .execute()
          .data or []
    )
    log.info("Updating prices for %d active watchlist rows.", len(rows))
    updated = 0
    for r in rows:
        ticker = r["ticker"]
        q = _finnhub_quote(ticker)
        if not isinstance(q, dict):
            continue
        price = q.get("c")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        entry = float(r.get("entry_price") or 0)
        ret = ((float(price) / entry) - 1) * 100 if entry > 0 else None
        peak = float(r.get("peak_return_pct") or 0)
        if ret is not None and ret > peak:
            peak = ret
        dd  = float(r.get("max_drawdown_pct") or 0)
        if ret is not None and peak > 0 and ret < peak:
            # Drawdown from the running peak (negative number).
            curr_dd = ret - peak
            if curr_dd < dd:
                dd = curr_dd
        market_cap = _finnhub_profile_market_cap(ticker)
        patch = _clean_for_json({
            "current_price": price,
            "current_market_cap": market_cap,
            "return_since_added": ret,
            "peak_return_pct": peak,
            "max_drawdown_pct": dd,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            db.table("emerging_watchlist").update(patch).eq("id", r["id"]).execute()
            updated += 1
        except Exception as exc:
            log.warning("  %s update failed: %s", ticker, exc)
        time.sleep(throttle_ms / 1000.0)
    log.info("Updated %d watchlist rows.", updated)
    return updated


# ══════════════════════════════════════════════════════════════════════════════
# Graduate + archive
# ══════════════════════════════════════════════════════════════════════════════

def graduate_oversized() -> int:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    rows = (
        db.table("emerging_watchlist")
          .select("id, ticker, current_market_cap")
          .gte("current_market_cap", GRADUATION_MARKET_CAP)
          .in_("status", ["active", "thesis_intact", "thesis_weakening"])
          .execute()
          .data or []
    )
    for r in rows:
        db.table("emerging_watchlist").update({"status": "graduated"}).eq("id", r["id"]).execute()
        log.info("  graduated %s (mc=%.0f)", r["ticker"], r["current_market_cap"] or 0)
    log.info("Graduated %d watchlist rows.", len(rows))
    return len(rows)


def archive_broken() -> int:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    rows = (
        db.table("emerging_watchlist")
          .select("id, ticker")
          .eq("status", "thesis_broken")
          .execute()
          .data or []
    )
    for r in rows:
        db.table("emerging_watchlist").update({"status": "archived"}).eq("id", r["id"]).execute()
        log.info("  archived %s", r["ticker"])
    log.info("Archived %d watchlist rows.", len(rows))
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--graduate", action="store_true")
    ap.add_argument("--archive", action="store_true")
    ap.add_argument("--throttle-ms", type=int, default=1100)
    args = ap.parse_args()

    did_anything = False
    if args.add:
        add_qualifying(throttle_ms=args.throttle_ms)
        did_anything = True
    if args.update:
        update_prices(throttle_ms=args.throttle_ms)
        did_anything = True
    if args.graduate:
        graduate_oversized()
        did_anything = True
    if args.archive:
        archive_broken()
        did_anything = True
    if not did_anything:
        log.info("Nothing to do. Use --add, --update, --graduate, --archive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
