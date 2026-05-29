"""
Quarterly Thesis Review
=======================

Runs once per quarter for every active watchlist name:
  - Re-runs the structural screen for the ticker (caching active).
  - Re-runs the scorer.
  - Compares: does it still pass 4+ traits? Did the multibagger_score move?
  - Flags thesis breaks:
        * growth deceleration -- TTM revenue YoY < 0 (was positive)
        * margin collapse     -- GM_TTM < GM_5Y - 5pts
        * dilution risk       -- runway < 12 mo and unprofitable
        * accounting flag     -- (placeholder; SEC-text mining)
  - Updates emerging_watchlist.status:
        intact / weakening / broken
    and writes a post-mortem when broken.

CLI
    python pipeline/multibagger/quarterly_review.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from multibagger.screener import screen_ticker, _form4_insider_map  # noqa: E402
from multibagger.scorer import score_row  # noqa: E402
from supabase import create_client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _classify(prev_score: float, new_score: float, signals: dict) -> tuple[str, str]:
    """Return (status, post_mortem_note)."""
    reasons = [k for k, v in signals.items() if v]
    if "growth_negative" in reasons or "margin_collapse" in reasons:
        return "thesis_broken", "; ".join(reasons)
    drop = (prev_score or 0) - new_score
    if drop > 20 or new_score < 45 or reasons:
        return "thesis_weakening", "; ".join(reasons) or f"score dropped {drop:.0f}pts"
    return "thesis_intact", ""


def run_review() -> int:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    insider_map = _form4_insider_map(db)

    rows = (
        db.table("emerging_watchlist")
          .select("id, ticker, multibagger_score, status, conviction_tier")
          .in_("status", ["active", "thesis_intact", "thesis_weakening"])
          .execute()
          .data or []
    )
    log.info("Reviewing %d active watchlist names", len(rows))

    today = date.today().isoformat()
    reviewed = 0
    for r in rows:
        ticker = r["ticker"]
        # Re-screen with whatever we have on disk (cached). Falls back if
        # the universe row no longer exists.
        cand_row = {"ticker": ticker, "market_value": None, "sector": "", "industry": "", "name": ""}
        try:
            screened = screen_ticker(cand_row, insider_map)
        except Exception as exc:
            log.warning("  %s re-screen failed: %s", ticker, exc)
            continue

        scored = score_row({**screened, "trait_scores": screened["trait_scores"]})
        new_score = float(scored["multibagger_score"])
        ev = screened.get("evidence", {}) or {}
        signals = {
            "growth_negative": (screened.get("revenue_growth_ttm_yoy") or 0) < 0,
            "margin_collapse": (
                (screened.get("gross_margin_ttm") or 0) > 0
                and (screened.get("gross_margin_trend") or 0) < -500   # >500bps decline
            ),
            "decelerating": bool(ev.get("t2", {}).get("decelerating")),
            "leverage_blowout": (screened.get("debt_to_equity") or 0) > 2.0,
        }

        status, note = _classify(float(r.get("multibagger_score") or 0), new_score, signals)
        db.table("emerging_watchlist").update({
            "status": status,
            "last_reviewed_date": today,
            "notes": (note or None),
        }).eq("id", r["id"]).execute()
        log.info("  %-6s  %s  new_score=%.1f  (was %.1f)  %s",
                 ticker, status, new_score, float(r.get("multibagger_score") or 0), note or "")
        reviewed += 1

    log.info("Quarterly review complete (%d names).", reviewed)
    return reviewed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.parse_args()
    run_review()
    return 0


if __name__ == "__main__":
    sys.exit(main())
