"""
Multibagger Tracker
===================

Maintains public.multibagger_outcomes -- the long-tail return record for
every name ever added to the watchlist. Used by the product to surface
HONEST track-record stats including the failures.

Operations:
    --refresh    For every watchlist row (any status), upsert an outcome
                 row with current return, peak return, drawdown, and
                 milestone dates (2x / 5x / 10x). Pulls latest prices via
                 Finnhub.
    --stats      Print engine-level stats:
                 - rows ever tracked
                 - % that hit 2x / 5x / 10x
                 - % archived as thesis_broken
                 - average current return
                 - median time-to-double for the winners that hit 2x

CLI
    python pipeline/multibagger/tracker.py --refresh
    python pipeline/multibagger/tracker.py --stats
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


def _quote(ticker: str) -> float | None:
    if not FINNHUB_API_KEY:
        return None
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": ticker, "token": FINNHUB_API_KEY},
            timeout=15,
        )
        if r.status_code == 200:
            obj = r.json() or {}
            p = obj.get("c")
            return float(p) if isinstance(p, (int, float)) and p > 0 else None
    except requests.RequestException:
        pass
    return None


def _clean(v):
    import math
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def refresh(throttle_ms: int = 1100) -> int:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    rows = (
        db.table("emerging_watchlist")
          .select("ticker, added_date, conviction_tier, entry_price, entry_market_cap, "
                  "current_price, return_since_added, peak_return_pct, max_drawdown_pct, status")
          .execute()
          .data or []
    )
    log.info("Refreshing tracker for %d watchlist rows", len(rows))
    today = date.today()
    out_count = 0
    for r in rows:
        ticker = r["ticker"]
        added = r.get("added_date")
        entry_price = float(r.get("entry_price") or 0)
        if entry_price <= 0:
            continue

        # Take the freshest price (watchlist row may have been updated this run)
        price = float(r.get("current_price") or 0) or _quote(ticker)
        if not price:
            continue

        ret = (price / entry_price - 1) * 100
        # Carry milestone dates forward
        existing = (
            db.table("multibagger_outcomes")
              .select("milestones")
              .eq("ticker", ticker)
              .eq("added_date", added)
              .maybe_single()
              .execute()
        )
        ms: dict = {}
        if existing and existing.data:
            ms = (existing.data.get("milestones") or {})
            if isinstance(ms, str):
                try:
                    ms = json.loads(ms)
                except Exception:
                    ms = {}
        for mult, key in ((100, "2x_date"), (400, "5x_date"), (900, "10x_date")):
            if ret >= mult and not ms.get(key):
                ms[key] = today.isoformat()

        holding_days = None
        try:
            d = datetime.fromisoformat(str(added)).date() if added else None
            if d:
                holding_days = (today - d).days
        except Exception:
            pass

        payload = _clean({
            "ticker": ticker,
            "added_date": added,
            "conviction_tier": r.get("conviction_tier"),
            "entry_price": entry_price,
            "entry_market_cap": r.get("entry_market_cap"),
            "current_price": price,
            "current_return_pct": ret,
            "peak_return_pct": max(float(r.get("peak_return_pct") or 0), ret),
            "max_drawdown_pct": float(r.get("max_drawdown_pct") or 0),
            "milestones": ms,
            "final_status": r.get("status") if r.get("status") in ("archived", "graduated") else None,
            "holding_period_days": holding_days,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })
        try:
            db.table("multibagger_outcomes").upsert(payload, on_conflict="tenant_id,ticker,added_date").execute()
            out_count += 1
        except Exception as exc:
            log.warning("  %s persist failed: %s", ticker, exc)
        time.sleep(throttle_ms / 1000.0)
    log.info("Refreshed %d outcome rows.", out_count)
    return out_count


def stats() -> dict:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    rows = db.table("multibagger_outcomes").select("*").execute().data or []
    n = len(rows)
    if n == 0:
        log.info("No outcomes tracked yet.")
        return {}
    twox = sum(1 for r in rows if (r.get("peak_return_pct") or 0) >= 100)
    fivex = sum(1 for r in rows if (r.get("peak_return_pct") or 0) >= 400)
    tenx = sum(1 for r in rows if (r.get("peak_return_pct") or 0) >= 900)
    broken = sum(1 for r in rows if r.get("final_status") == "archived")
    avg_ret = sum((r.get("current_return_pct") or 0) for r in rows) / n
    days_to_2x = []
    for r in rows:
        ms = r.get("milestones") or {}
        if isinstance(ms, str):
            try:
                ms = json.loads(ms)
            except Exception:
                ms = {}
        if ms.get("2x_date") and r.get("added_date"):
            try:
                d0 = datetime.fromisoformat(str(r["added_date"])).date()
                d1 = datetime.fromisoformat(str(ms["2x_date"])).date()
                days_to_2x.append((d1 - d0).days)
            except Exception:
                pass
    days_to_2x.sort()
    median_t_2x = days_to_2x[len(days_to_2x) // 2] if days_to_2x else None
    out = {
        "names_tracked": n,
        "hit_2x_pct": round(100 * twox / n, 1),
        "hit_5x_pct": round(100 * fivex / n, 1),
        "hit_10x_pct": round(100 * tenx / n, 1),
        "broken_pct": round(100 * broken / n, 1),
        "avg_current_return_pct": round(avg_ret, 2),
        "median_days_to_2x": median_t_2x,
    }
    log.info("Tracker stats:\n  %s",
             "\n  ".join(f"{k}: {v}" for k, v in out.items()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--throttle-ms", type=int, default=1100)
    args = ap.parse_args()
    if args.refresh:
        refresh(throttle_ms=args.throttle_ms)
    if args.stats:
        stats()
    if not (args.refresh or args.stats):
        stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
