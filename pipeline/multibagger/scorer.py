"""
Stage 2 -- Multibagger Score
============================

Reads the screener output and assigns each candidate a 0-100 multibagger_score
across five components:

    growth_quality        (30 %)
    reinvestment_runway   (25 %)
    unit_economics        (20 %)
    alignment             (15 %)
    discovery_gap         (10 %)

then applies hard quality caps that REDUCE the score for known risks (caps,
never bonuses). This intentionally penalises growth-at-any-cost names that
ace the screener but have unacceptable balance-sheet or accounting risk.

CAPS
    cash_runway < 12 months AND not yet profitable   → cap 50  (dilution risk)
    debt/equity > 2.0                                → cap 40  (over-levered)
    revenue growth decelerating 2 consecutive Qs     → cap 50
    accounting red flags (restatement/auditor/...)   → EXCLUDE

The top 30 by multibagger_score are marked advanced_to_research = true and
flow into deep_research.py.

CLI
    python pipeline/multibagger/scorer.py
    python pipeline/multibagger/scorer.py --top-n 30
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
from supabase import create_client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

WEIGHTS = {
    "growth_quality":   0.30,
    "reinvestment":     0.25,
    "unit_economics":   0.20,
    "alignment":        0.15,
    "discovery":        0.10,
}


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _clip(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


# ══════════════════════════════════════════════════════════════════════════════
# Component scores
# ══════════════════════════════════════════════════════════════════════════════

def growth_quality(row: dict) -> float:
    cagr = _num(row.get("revenue_cagr_3y"))
    ttm  = _num(row.get("revenue_growth_ttm_yoy"))
    durable = _num((row.get("trait_scores") or {}).get("durable_growth")) or 0
    base = 0.0
    if cagr is not None:
        if cagr >= 40:
            base = 95
        elif cagr >= 25:
            base = 80
        elif cagr >= 15:
            base = 55
        elif cagr >= 5:
            base = 25
        else:
            base = 5
    else:
        base = durable  # fall back to trait score if CAGR missing
    if ttm is not None and ttm >= 30 and base < 90:
        base += 5
    if ttm is not None and ttm < 0:
        base *= 0.4
    return _clip(base)


def reinvestment_runway(row: dict) -> float:
    """Proxy: tiny-base growth names with high gross margins (signal of
    reinvestable economics) and growth sectors. The LLM analyst refines
    TAM in Stage 3 -- this is a structural prior."""
    market_cap = _num(row.get("market_cap")) or 0
    gm_ttm = _num(row.get("gross_margin_ttm"))
    tam_proxy = _num((row.get("trait_scores") or {}).get("large_tam_proxy")) or 50
    score = tam_proxy
    if market_cap > 0 and market_cap < 500e6:
        score += 15
    elif market_cap < 2e9:
        score += 5
    if gm_ttm is not None:
        if gm_ttm >= 60:
            score += 15
        elif gm_ttm >= 40:
            score += 8
    return _clip(score)


def unit_economics(row: dict) -> float:
    """Re-uses the trait-3 score directly; the trait already encodes the
    level + trend logic we care about."""
    return _num((row.get("trait_scores") or {}).get("improving_unit_econ")) or 0


def alignment(row: dict) -> float:
    return _num((row.get("trait_scores") or {}).get("aligned_owners")) or 50


def discovery(row: dict) -> float:
    return _num((row.get("trait_scores") or {}).get("under_discovered")) or 50


# ══════════════════════════════════════════════════════════════════════════════
# Caps
# ══════════════════════════════════════════════════════════════════════════════

def apply_caps(score: float, row: dict) -> tuple[float, list[dict]]:
    caps: list[dict] = []
    runway = _num(row.get("cash_runway_months"))
    om_ttm = _num(row.get("operating_margin_ttm"))
    if runway is not None and runway < 12 and (om_ttm is None or om_ttm < 0):
        if score > 50:
            caps.append({"cap": 50, "reason": "cash_runway<12mo and not yet profitable"})
            score = 50

    d2e = _num(row.get("debt_to_equity"))
    if d2e is not None and d2e > 2.0:
        if score > 40:
            caps.append({"cap": 40, "reason": f"debt/equity {d2e:.2f} > 2.0"})
            score = 40

    # Deceleration cap -- trait_scores encoded it via the durable_growth halving
    ev = (row.get("evidence") or {}).get("t2", {}) if row.get("evidence") else {}
    if ev.get("decelerating"):
        if score > 50:
            caps.append({"cap": 50, "reason": "revenue growth decelerating QoQ"})
            score = 50

    # Accounting red flags would normally come from sec_filings text mining
    # (restatement, auditor change, going concern). Stub: not wired yet --
    # the deep_research stage will flag these via LLM. Document the contract.
    return score, caps


# ══════════════════════════════════════════════════════════════════════════════
# Score one candidate row
# ══════════════════════════════════════════════════════════════════════════════

def score_row(row: dict) -> dict:
    # Expand notes->evidence if persisted as JSON
    if isinstance(row.get("notes"), str):
        try:
            row["evidence"] = json.loads(row["notes"]).get("evidence")
        except Exception:
            pass

    gq = growth_quality(row)
    rr = reinvestment_runway(row)
    ue = unit_economics(row)
    al = alignment(row)
    di = discovery(row)

    raw = (
        gq * WEIGHTS["growth_quality"]
        + rr * WEIGHTS["reinvestment"]
        + ue * WEIGHTS["unit_economics"]
        + al * WEIGHTS["alignment"]
        + di * WEIGHTS["discovery"]
    )
    capped, caps = apply_caps(raw, row)
    return {
        "id": row.get("id"),
        "ticker": row["ticker"],
        "growth_quality_score": round(gq, 1),
        "reinvestment_score": round(rr, 1),
        "unit_economics_score": round(ue, 1),
        "alignment_score": round(al, 1),
        "discovery_score": round(di, 1),
        "multibagger_score": round(capped, 1),
        "raw_score_pre_caps": round(raw, 1),
        "score_caps_applied": caps,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Persist + select top N
# ══════════════════════════════════════════════════════════════════════════════

def run_scoring(top_n: int = 30, screen_date: str | None = None) -> list[dict]:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Latest screen
    if screen_date is None:
        last = (
            db.table("multibagger_candidates")
              .select("screen_date")
              .order("screen_date", desc=True)
              .limit(1)
              .execute()
              .data or []
        )
        if not last:
            raise SystemExit("No screener rows found -- run screener.py first.")
        screen_date = last[0]["screen_date"]
    log.info("Scoring candidates with screen_date=%s", screen_date)

    rows = (
        db.table("multibagger_candidates")
          .select("*")
          .eq("screen_date", screen_date)
          .gte("traits_passed", 4)        # only score names that at least 'watch'
          .execute()
          .data or []
    )
    log.info("  %d candidates scoring", len(rows))

    scored = [score_row(r) for r in rows]
    scored.sort(key=lambda r: r["multibagger_score"], reverse=True)

    top_ids = {r["id"] for r in scored[:top_n] if r.get("id") is not None}

    # Persist scores + advance flag
    for r in scored:
        if r.get("id") is None:
            continue
        db.table("multibagger_candidates").update({
            "multibagger_score": r["multibagger_score"],
            "growth_quality_score": r["growth_quality_score"],
            "reinvestment_score": r["reinvestment_score"],
            "unit_economics_score": r["unit_economics_score"],
            "alignment_score": r["alignment_score"],
            "discovery_score": r["discovery_score"],
            "score_caps_applied": r["score_caps_applied"],
            "advanced_to_research": r["id"] in top_ids,
        }).eq("id", r["id"]).execute()

    log.info("Top %d by multibagger_score:", top_n)
    for r in scored[:top_n]:
        caps = ", ".join(c["reason"] for c in r["score_caps_applied"]) or "—"
        log.info("  %-6s  score=%5.1f  gq=%4.1f rr=%4.1f ue=%4.1f al=%4.1f di=%4.1f  caps: %s",
                 r["ticker"], r["multibagger_score"],
                 r["growth_quality_score"], r["reinvestment_score"],
                 r["unit_economics_score"], r["alignment_score"], r["discovery_score"],
                 caps)
    return scored


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument("--screen-date", type=str, default=None)
    ap.parse_args()
    args = ap.parse_args()
    run_scoring(top_n=args.top_n, screen_date=args.screen_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
