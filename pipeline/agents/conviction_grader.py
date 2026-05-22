"""
Conviction Grader
Assigns each focus-list pick a conviction grade (A / B / C / D) by consolidating
every signal tier the pipeline produces, then applying hard caps.

This agent is referenced by critic_agent, the GARCH desk and Black-Litterman as
the place where their verdicts are actually ENFORCED. Until now each of those
agents only *stored* its verdict; this is where the caps bite.

SCORING  (conviction_score = base + quant bonuses)
  base                 ranked_focus_list.composite_score (the ranking score)
  + 5   Monte Carlo prob_up > 0.65
  + 3   VaR not in the worst quartile of the scored set
  + 8   Fama-French significant positive alpha with R-squared > 0.50
  + 6   DCF shows 20-50% upside with peer multiples consistent
  + 0   DCF shows > 50% upside  (suspicious -- deliberately not rewarded)

GRADE  (percentile of conviction_score, then capped)
  provisional: top 20% -> A, next 30% -> B, next 30% -> C, rest -> D
  HARD CAPS (a grade can only move DOWN):
    critic STRONG objection            -> max B
    GARCH regime 'crisis'              -> max C
    GARCH regime 'elevated'            -> max B
    Monte Carlo calibration < 0.60     -> max B (not A-eligible)
    cross-validation contradiction     -> max B
  critic MODERATE objection keeps A but sets advisor_review = true.

Writes conviction_grade / conviction_score / quant_signal_summary onto
ranked_focus_list (migration 019).

Usage:
    python pipeline/agents/conviction_grader.py [premarket|midday|close]
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _fetch_all(query_fn, page=1000):
    rows, start = [], 0
    while True:
        chunk = query_fn().range(start, start + page - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        start += page


def _latest_by_ticker(rows, time_key):
    out = {}
    for r in sorted(rows, key=lambda x: x.get(time_key) or "", reverse=True):
        out.setdefault(r.get("ticker"), r)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Per-model traffic light  (green / amber / red) -- drives the Quant Verdict
# ══════════════════════════════════════════════════════════════════════════════

def _model_lights(mc, var, garch, ff, dcf) -> dict:
    lights = {}

    if mc:
        pu = _num(mc.get("prob_up")) or 0
        cal = _num(mc.get("calibration_quality")) or 0
        lights["monte_carlo"] = ("green" if pu > 0.6 and cal >= 0.6
                                 else "red" if pu < 0.4 else "amber")
    else:
        lights["monte_carlo"] = "amber"

    if var:
        v = abs(_num(var.get("daily_var_95")) or 0)
        lights["var"] = ("green" if v < 0.04 else "red" if v > 0.08 else "amber")
    else:
        lights["var"] = "amber"

    if garch:
        regime = garch.get("regime")
        q = garch.get("model_quality")
        lights["garch"] = ("red" if regime == "crisis"
                           else "amber" if regime == "elevated" or q == "unreliable"
                           else "green")
    else:
        lights["garch"] = "amber"

    if ff:
        sig = ff.get("alpha_significance")
        r2 = _num(ff.get("r_squared")) or 0
        lights["fama_french"] = ("green" if sig == "significant_positive" and r2 > 0.5
                                 else "red" if sig == "significant_negative"
                                 else "amber")
    else:
        lights["fama_french"] = "amber"

    if dcf and dcf.get("valuation_label") not in (None, "not_applicable"):
        label = dcf.get("valuation_label")
        conf = dcf.get("confidence")
        lights["dcf"] = ("green" if label in ("undervalued", "deeply_undervalued")
                         and conf != "low"
                         else "red" if label == "deeply_overvalued" else "amber")
    else:
        lights["dcf"] = "amber"
    return lights


# ══════════════════════════════════════════════════════════════════════════════
# Grading
# ══════════════════════════════════════════════════════════════════════════════

_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}
_REV = {v: k for k, v in _ORDER.items()}


def _cap(grade, ceiling):
    """Move `grade` down to at most `ceiling` (never up)."""
    return _REV[max(_ORDER[grade], _ORDER[ceiling])]


def run(run_type: str = "midday") -> None:
    if run_type not in ("premarket", "midday", "close"):
        run_type = "midday"
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Conviction Grader -- run_type=%s", run_type)

    recent = (db.table("ranked_focus_list").select("run_date")
              .order("run_date", desc=True).limit(1).execute().data)
    if not recent:
        log.warning("ranked_focus_list empty -- run ranking_engine first.")
        return
    run_date = recent[0]["run_date"]
    picks = (db.table("ranked_focus_list")
             .select("id,ticker,rank,composite_score,critic_objection_level")
             .eq("run_date", run_date).eq("run_type", run_type)
             .order("rank").execute().data or [])
    if not picks:
        log.warning("no picks for %s/%s", run_date, run_type)
        return
    tickers = [p["ticker"] for p in picks]
    log.info("Grading %d picks from %s/%s", len(picks), run_date, run_type)

    # ── Load every signal source (graceful if a table is empty) ───────────────
    def _safe(fn, label):
        try:
            return fn()
        except Exception as exc:
            log.warning("%s load failed: %s", label, exc)
            return {}

    validated = _latest_by_ticker(_safe(lambda: _fetch_all(
        lambda: db.table("validated_signals").select(
            "ticker,validated_strength,contradiction_flag,snapshot_time")),
        "validated_signals") or [], "snapshot_time")
    mc = _latest_by_ticker(_safe(lambda: _fetch_all(
        lambda: db.table("monte_carlo_results").select(
            "ticker,prob_up,calibration_quality,horizon_days,run_date")
        .eq("horizon_days", 30)), "monte_carlo_results") or [], "run_date")
    var = _latest_by_ticker(_safe(lambda: _fetch_all(
        lambda: db.table("ticker_risk_metrics").select(
            "ticker,daily_var_95,calculation_date")), "ticker_risk_metrics") or [],
        "calculation_date")
    garch = _latest_by_ticker(_safe(lambda: _fetch_all(
        lambda: db.table("garch_forecasts").select(
            "ticker,regime,model_quality,forecast_date")), "garch_forecasts") or [],
        "forecast_date")
    ff = _latest_by_ticker(_safe(lambda: _fetch_all(
        lambda: db.table("factor_attribution").select(
            "ticker,alpha_significance,r_squared,alpha_annual,calculation_date")),
        "factor_attribution") or [], "calculation_date")
    dcf = _latest_by_ticker(_safe(lambda: _fetch_all(
        lambda: db.table("dcf_valuations").select(
            "ticker,valuation_label,upside_to_dcf_pct,peer_multiple_check,"
            "confidence,calculation_date")), "dcf_valuations") or [],
        "calculation_date")

    # VaR peer reference: the worst-quartile cutoff across the scored set.
    var_vals = sorted(abs(_num(v.get("daily_var_95")) or 0)
                      for v in var.values() if v.get("daily_var_95") is not None)
    var_worst_cut = (var_vals[int(len(var_vals) * 0.75)]
                     if len(var_vals) >= 4 else None)

    # ── Score every pick ──────────────────────────────────────────────────────
    graded = []
    for p in picks:
        t = p["ticker"]
        base = _num(p.get("composite_score")) or 0.0
        bonuses, caps = [], []
        score = base

        m = mc.get(t)
        if m and (_num(m.get("prob_up")) or 0) > 0.65:
            score += 5; bonuses.append("MC prob_up>0.65 (+5)")

        v = var.get(t)
        if v is not None and var_worst_cut is not None:
            if abs(_num(v.get("daily_var_95")) or 0) <= var_worst_cut:
                score += 3; bonuses.append("VaR not worst-quartile (+3)")

        f = ff.get(t)
        if f and f.get("alpha_significance") == "significant_positive" \
                and (_num(f.get("r_squared")) or 0) > 0.50:
            score += 8; bonuses.append("FF significant +alpha, R2>0.5 (+8)")

        d = dcf.get(t)
        if d and d.get("upside_to_dcf_pct") is not None:
            up = _num(d.get("upside_to_dcf_pct"))
            if 0.20 < up <= 0.50 and d.get("peer_multiple_check") == "consistent_with_peers":
                score += 6; bonuses.append("DCF 20-50% upside, peer-consistent (+6)")
            elif up > 0.50:
                bonuses.append("DCF >50% upside -- not rewarded (suspicious)")

        # Hard-cap conditions.
        ceilings = []
        if (p.get("critic_objection_level") or "").upper() == "STRONG":
            ceilings.append("B"); caps.append("critic STRONG objection -> max B")
        advisor_review = (p.get("critic_objection_level") or "").upper() == "MODERATE"
        g = garch.get(t)
        if g and g.get("regime") == "crisis":
            ceilings.append("C"); caps.append("GARCH crisis regime -> max C")
        elif g and g.get("regime") == "elevated":
            ceilings.append("B"); caps.append("GARCH elevated regime -> max B")
        if m and (_num(m.get("calibration_quality")) or 1) < 0.60:
            ceilings.append("B"); caps.append("MC calibration <0.60 -> max B")
        vs = validated.get(t)
        if vs and vs.get("contradiction_flag"):
            ceilings.append("B"); caps.append("cross-validation contradiction -> max B")

        graded.append({"pick": p, "ticker": t, "score": round(score, 3),
                       "bonuses": bonuses, "ceilings": ceilings, "caps": caps,
                       "advisor_review": advisor_review,
                       "lights": _model_lights(m, v, g, f, d),
                       "_mc": m, "_var": v, "_garch": g, "_ff": f, "_dcf": d})

    # ── Provisional grade by percentile, then apply caps ──────────────────────
    graded.sort(key=lambda x: x["score"], reverse=True)
    n = len(graded)
    for i, gr in enumerate(graded):
        pct = i / n
        provisional = ("A" if pct < 0.20 else "B" if pct < 0.50
                       else "C" if pct < 0.80 else "D")
        final = provisional
        for ceil in gr["ceilings"]:
            final = _cap(final, ceil)
        gr["provisional_grade"] = provisional
        gr["conviction_grade"] = final

    # ── Persist ───────────────────────────────────────────────────────────────
    updated = 0
    for gr in graded:
        summary = {
            "conviction_score": gr["score"],
            "provisional_grade": gr["provisional_grade"],
            "final_grade": gr["conviction_grade"],
            "bonuses_applied": gr["bonuses"],
            "caps_applied": gr["caps"],
            "advisor_review": gr["advisor_review"],
            "model_lights": gr["lights"],
        }
        try:
            db.table("ranked_focus_list").update({
                "conviction_grade": gr["conviction_grade"],
                "conviction_score": gr["score"],
                "quant_signal_summary": summary,
            }).eq("run_date", run_date).eq("ticker", gr["ticker"]) \
              .eq("run_type", run_type).execute()
            updated += 1
        except Exception as exc:
            log.debug("update %s failed: %s", gr["ticker"], exc)
    log.info("ranked_focus_list: conviction grade written for %d/%d", updated, len(graded))
    if updated == 0:
        log.warning("Nothing persisted -- apply migration 019_conviction_columns.sql")

    _report(graded)


def _report(graded) -> None:
    bar = "=" * 76
    print(f"\n{bar}\nCONVICTION GRADING -- {len(graded)} picks\n{bar}")
    dist = {"A": 0, "B": 0, "C": 0, "D": 0}
    for g in graded:
        dist[g["conviction_grade"]] += 1
    print(f"\n[ GRADE DISTRIBUTION ]  A={dist['A']}  B={dist['B']}  "
          f"C={dist['C']}  D={dist['D']}")

    print("\n[ TOP 15 BY CONVICTION SCORE ]")
    print(f"  {'TICKER':<8}{'SCORE':>8}{'PROV':>6}{'FINAL':>7}  CAPS / NOTES")
    for g in graded[:15]:
        notes = "; ".join(g["caps"]) if g["caps"] else \
                ("MODERATE objection -> advisor review" if g["advisor_review"] else "")
        moved = " " if g["provisional_grade"] == g["conviction_grade"] else "v"
        print(f"  {g['ticker']:<8}{g['score']:>8.1f}{g['provisional_grade']:>6}"
              f"{g['conviction_grade']:>6}{moved}  {notes}")

    capped = [g for g in graded if g["provisional_grade"] != g["conviction_grade"]]
    print(f"\n[ DOWNGRADED BY HARD CAPS -- {len(capped)} ]")
    for g in capped:
        print(f"  {g['ticker']:<8}{g['provisional_grade']} -> {g['conviction_grade']}"
              f"   {'; '.join(g['caps'])}")
    print(bar + "\n")


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
