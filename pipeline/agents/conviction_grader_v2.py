"""
Conviction Grader v2
The final gate before reports are written. Only A-graded picks lead the brief.

This is a simpler, advisor-facing grader than v1 (conviction_grader.py):
v1 percentile-grades a quant-bonus score (MC / VaR / FF / DCF); v2 starts
from validated_strength and modulates it with the signals an advisor argues
about in a meeting -- pattern-match win rate, catalyst clarity, the critic's
objection level, factor concentration.

INPUTS PER TICKER
  validated_strength             validated_signals.validated_strength
                                 -- the cross_validator emits this on a small
                                    raw scale (~0.5..2.5). The grading
                                    thresholds are written for a 0-100 base,
                                    so we min-max scale today's set into
                                    0..100 before applying any multiplier
                                    (today's strongest -> 100, weakest -> 0).
  catalyst_confidence            ranked_focus_list.catalyst_confidence
  critic_objection_level         ranked_focus_list.critic_objection_level
  portfolio_fit recommendation   portfolio_fit.recommendation (per portfolio,
                                 then aggregated -- "high_priority"/"consider"
                                 hits net positive, "pass" hits net negative)
  pattern_match win_rate_5d      pattern_matches.win_rate_5d
  factor concentration warning   triggered if EITHER:
                                   * ticker's sector or any factor_tag matches
                                     a string in today's factor_concentration
                                     .concentration_alerts, OR
                                   * any portfolio_fit row for the ticker has
                                     sector_fit_score < 70

GRADE
  score = validated_strength
  if critic == STRONG / STRONG_OBJECTION:  cap at C (hard floor)
  if win_rate_5d < 0.40:                   score *= 0.85
  if win_rate_5d > 0.70:                   score *= 1.10
  if catalyst_confidence > 0.8:            score *= 1.05
  if factor_concentration_warning:         score *= 0.95
  -- A requires score >= 75 AND critic == NONE
  -- B requires score >= 55
  -- C requires score >= 40
  -- otherwise the ticker drops from the brief (NULL grade)

HARD COUNT LIMITS
  At most 5 A, 7 B, 5 C in the brief. Excess (by score) is dropped from the
  brief (NULL grade) -- the spec says "force selectivity", not "demote".

Writes onto ranked_focus_list (migration 025):
  conviction_grade            -- 'A' | 'B' | 'C' | NULL
  conviction_score_adjusted   -- numeric after multipliers
  grade_reasoning             -- one-sentence advisor-facing justification

Usage:
    python pipeline/agents/conviction_grader_v2.py [premarket|midday|close]
"""

import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

MAX_A = 5
MAX_B = 7
MAX_C = 5

THRESH_A = 75
THRESH_B = 55
THRESH_C = 40

WIN_LOW  = 0.40
WIN_HIGH = 0.70
CATALYST_CLEAR = 0.8
SECTOR_FIT_WARN = 70

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

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


def _normalize_critic(level):
    """The schema comment says STRONG | MODERATE | NONE, but the user's spec
    uses STRONG_OBJECTION. Accept both."""
    if not level:
        return "NONE"
    u = str(level).upper().strip()
    if u in ("STRONG", "STRONG_OBJECTION"):
        return "STRONG"
    if u in ("MODERATE", "MODERATE_OBJECTION"):
        return "MODERATE"
    return "NONE"


# ══════════════════════════════════════════════════════════════════════════════
# Concentration-warning detection -- the only non-trivial input
# ══════════════════════════════════════════════════════════════════════════════

def _build_concentration_index(db, run_date, run_type):
    """Returns (alert_strings_lower, sector_overlimit_set). The alert strings
    come from factor_concentration.concentration_alerts -- short human-readable
    strings like 'tech-heavy: 18/30 picks' or 'large-cap concentration 75%'.
    We match a ticker by case-insensitive substring on its sector or any
    factor_tag from factor_exposure."""
    try:
        rows = (db.table("factor_concentration")
                .select("concentration_alerts")
                .eq("run_date", run_date).eq("run_type", run_type)
                .limit(1).execute().data or [])
    except Exception:
        rows = []
    alerts = []
    if rows:
        alerts = rows[0].get("concentration_alerts") or []
    return [str(a).lower() for a in alerts]


def _ticker_concentration_warning(ticker, alerts_lower, fexp, pfit_rows) -> bool:
    """Rule 1: ticker's sector or factor_tags appears in any alert string.
       Rule 2: any portfolio_fit row for this ticker has sector_fit_score < 70.
       Either triggers a warning."""
    fe = fexp.get(ticker) or {}
    sector = (fe.get("sector") or "").lower()
    tags = [str(t).lower() for t in (fe.get("factor_tags") or [])]
    for a in alerts_lower:
        if sector and sector in a:
            return True
        if any(tag and tag in a for tag in tags):
            return True
    for r in pfit_rows.get(ticker, []):
        sfs = _num(r.get("sector_fit_score"))
        if sfs is not None and sfs < SECTOR_FIT_WARN:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# Per-ticker grading
# ══════════════════════════════════════════════════════════════════════════════

def _grade_one(t, inputs):
    """Returns dict: score, grade (or None), reasoning, multipliers.

    inputs["validated_strength"] is the ALREADY-SCALED 0..100 base (the
    caller min-max scales the raw cross_validator output within today's set
    before invoking this function)."""
    vs = _num(inputs["validated_strength"]) or 0.0
    critic = inputs["critic"]
    win5 = _num(inputs["win_rate_5d"])
    cat_conf = _num(inputs["catalyst_confidence"])
    concentration_warn = inputs["concentration_warn"]

    score = vs
    applied = []   # [(label, multiplier)] -- for reasoning

    # Critic STRONG forces C cap (applied AFTER score math so reasoning still mentions multipliers).
    critic_caps_c = (critic == "STRONG")

    if win5 is not None and win5 < WIN_LOW:
        score *= 0.85; applied.append((f"poor 5-day analogs (win_rate {win5:.0%})", 0.85))
    elif win5 is not None and win5 > WIN_HIGH:
        score *= 1.10; applied.append((f"strong 5-day analogs (win_rate {win5:.0%})", 1.10))

    if cat_conf is not None and cat_conf > CATALYST_CLEAR:
        score *= 1.05; applied.append((f"clear catalyst (conf {cat_conf:.2f})", 1.05))

    if concentration_warn:
        score *= 0.95; applied.append(("factor concentration warning", 0.95))

    # Grade by threshold (provisional, before count caps).
    if critic_caps_c:
        grade = "C"      # critic STRONG floors at C regardless of score
    elif score >= THRESH_A and critic == "NONE":
        grade = "A"
    elif score >= THRESH_B:
        grade = "B"
    elif score >= THRESH_C:
        grade = "C"
    else:
        grade = None

    return {
        "score":        round(score, 2),
        "base_score":   round(vs, 2),
        "grade":        grade,
        "applied":      applied,
        "critic":       critic,
        "critic_caps_c": critic_caps_c,
        "win5":         win5,
        "cat_conf":     cat_conf,
        "concentration_warn": concentration_warn,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Reasoning sentence
# ══════════════════════════════════════════════════════════════════════════════

def _reason(t, g, validated, pattern, pick) -> str:
    """One-sentence advisor-facing justification."""
    grade = g["grade"]
    parts = []

    # Signal-confirmation count from validated_signals.
    vs_row = validated.get(t) or {}
    n_conf = vs_row.get("confirmation_count")
    if n_conf is not None:
        parts.append(f"{n_conf}/6 signals confirmed")

    # Catalyst.
    cat_cat  = pick.get("catalyst_category")
    cat_desc = pick.get("catalyst_description") or ""
    if cat_cat and cat_cat != "none":
        cat_short = cat_desc.split(".")[0][:80] if cat_desc else cat_cat
        if g["cat_conf"] is not None and g["cat_conf"] > CATALYST_CLEAR:
            parts.append(f"strong catalyst ({cat_cat})")
        else:
            parts.append(f"catalyst: {cat_cat}")

    # Pattern analogs.
    pm = pattern.get(t) or {}
    avg5 = _num(pm.get("avg_5d_return"))
    if avg5 is not None:
        if g["win5"] is not None and g["win5"] > WIN_HIGH:
            parts.append(f"historical analogs {avg5*100:+.1f}% avg")
        elif g["win5"] is not None and g["win5"] < WIN_LOW:
            parts.append(f"weak analogs ({avg5*100:+.1f}% avg)")

    # Critic.
    if g["critic"] == "STRONG":
        parts.append("critic flagged a STRONG objection")
    elif g["critic"] == "MODERATE":
        parts.append("critic raised a moderate concern")
    elif g["critic"] == "NONE":
        parts.append("critic raised no major objections")

    # Concentration.
    if g["concentration_warn"]:
        parts.append("factor-concentration risk noted")

    body = ", ".join(parts) if parts else "no decisive signals"
    if grade is None:
        return f"Dropped: base score {g['base_score']}; {body}."
    if g["critic_caps_c"] and grade == "C":
        return (f"C: Despite base score {g['base_score']}, critic STRONG "
                f"objection capped the grade; {body}.")
    return f"{grade}: score {g['score']}; {body}."


# ══════════════════════════════════════════════════════════════════════════════
# Source loading
# ══════════════════════════════════════════════════════════════════════════════

def _load_inputs(db, run_date, run_type, picks):
    tickers = [p["ticker"] for p in picks]

    validated = _latest_by_ticker(
        _fetch_all(lambda: db.table("validated_signals")
                   .select("ticker,validated_strength,confirmation_count,"
                           "contradiction_flag,snapshot_time")
                   .in_("ticker", tickers).eq("run_type", run_type)),
        "snapshot_time")

    pattern = _latest_by_ticker(
        _fetch_all(lambda: db.table("pattern_matches")
                   .select("ticker,win_rate_5d,avg_5d_return,confidence,"
                           "snapshot_date")
                   .in_("ticker", tickers)),
        "snapshot_date")

    # factor_exposure latest per ticker.
    fexp_rows = _fetch_all(lambda: db.table("factor_exposure")
                            .select("ticker,sector,factor_tags,snapshot_date")
                            .in_("ticker", tickers)
                            .order("snapshot_date", desc=True))
    fexp = {}
    for r in fexp_rows:
        fexp.setdefault(r["ticker"], r)

    # portfolio_fit -- one row per (portfolio, ticker, snapshot_date). Take
    # the latest snapshot for each (portfolio, ticker), then group by ticker.
    pfit_rows_raw = _fetch_all(lambda: db.table("portfolio_fit")
                                .select("portfolio_id,ticker,recommendation,"
                                        "sector_fit_score,snapshot_date")
                                .in_("ticker", tickers)
                                .order("snapshot_date", desc=True))
    by_pt = {}
    for r in pfit_rows_raw:
        key = (r["portfolio_id"], r["ticker"])
        by_pt.setdefault(key, r)
    pfit = {}
    for (_, t), r in by_pt.items():
        pfit.setdefault(t, []).append(r)

    # Smart Money intel (Step 6.7). Latest snapshot per ticker.
    smart_money = {}
    try:
        sm_rows = _fetch_all(lambda: db.table("smart_money_intel")
                              .select("ticker,pattern_detected,confidence_level,"
                                      "composite_smart_money_score,quality_grade,"
                                      "snapshot_date")
                              .in_("ticker", tickers)
                              .order("snapshot_date", desc=True))
        for r in sm_rows:
            smart_money.setdefault(r["ticker"], r)
    except Exception as exc:
        log.debug("smart_money_intel preload failed: %s", exc)

    return validated, pattern, fexp, pfit, smart_money


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday") -> None:
    if run_type not in ("premarket", "midday", "close"):
        run_type = "midday"
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Conviction Grader v2 -- run_type=%s", run_type)

    recent = (db.table("ranked_focus_list").select("run_date")
              .order("run_date", desc=True).limit(1).execute().data)
    if not recent:
        log.warning("ranked_focus_list empty -- run ranking_engine first.")
        return
    run_date = recent[0]["run_date"]
    picks = (db.table("ranked_focus_list")
             .select("id,ticker,rank,composite_score,catalyst_category,"
                     "catalyst_description,catalyst_confidence,"
                     "critic_objection_level")
             .eq("run_date", run_date).eq("run_type", run_type)
             .order("rank").execute().data or [])
    if not picks:
        log.warning("no picks for %s/%s", run_date, run_type)
        return
    log.info("Grading %d picks from %s/%s", len(picks), run_date, run_type)

    validated, pattern, fexp, pfit, smart_money = _load_inputs(db, run_date, run_type, picks)
    alerts_lower = _build_concentration_index(db, run_date, run_type)

    # ── Min-max scale validated_strength into 0..100 across today's picks ────
    raw_strengths = [_num((validated.get(p["ticker"]) or {}).get("validated_strength"))
                     for p in picks]
    present = [v for v in raw_strengths if v is not None]
    if present:
        lo, hi = min(present), max(present)
        span = (hi - lo) or 1.0
    else:
        lo, span = 0.0, 1.0

    def _scaled(raw):
        if raw is None:
            return 0.0
        return ((raw - lo) / span) * 100.0

    log.info("validated_strength range: [%.3f, %.3f] -> scaled to [0, 100]", lo, lo + span)

    # ── Per-ticker scoring ────────────────────────────────────────────────────
    graded = []
    for p in picks:
        t = p["ticker"]
        vs_row = validated.get(t) or {}
        pm_row = pattern.get(t) or {}
        raw_strength = _num(vs_row.get("validated_strength"))
        concentration_warn = _ticker_concentration_warning(
            t, alerts_lower, fexp, pfit)
        inputs = {
            "validated_strength":   _scaled(raw_strength),
            "critic":               _normalize_critic(p.get("critic_objection_level")),
            "win_rate_5d":          _num(pm_row.get("win_rate_5d")),
            "catalyst_confidence":  _num(p.get("catalyst_confidence")),
            "concentration_warn":   concentration_warn,
        }
        g = _grade_one(t, inputs)
        g["raw_validated_strength"] = raw_strength
        g["pick"]     = p
        g["ticker"]   = t
        g["reason"]   = _reason(t, g, validated, pattern, p)
        # ── Step 6.7 Smart Money overlay ─────────────────────────────────────
        sm = smart_money.get(t)
        sm_notes = []
        opportunistic_tag = False
        if sm:
            pat = sm.get("pattern_detected")
            conf = sm.get("confidence_level")
            comp = _num(sm.get("composite_smart_money_score")) or 0
            qg   = sm.get("quality_grade")
            # LOW confidence is informational only -- annotate, do not change grade.
            informational = (conf == "LOW")
            if not informational and qg != "UNVERIFIED":
                # Score boost / cap by composite
                if comp > 60 and conf == "HIGH":
                    g["score"] = round(g["score"] + 10, 2)
                    sm_notes.append(f"smart-money composite {comp} (HIGH) +10")
                    g["grade"] = _regrade_from_score(g["score"], g["critic"])
                elif comp < -60 and conf == "HIGH":
                    if g["grade"] in ("A", "B"):
                        sm_notes.append(f"smart-money composite {comp} (HIGH) caps at C")
                        g["grade"] = "C"
                # Pattern caps
                if pat == "INSIDER_SMOKE_SIGNAL" and g["grade"] == "A":
                    sm_notes.append("INSIDER_SMOKE_SIGNAL pattern caps at B")
                    g["grade"] = "B"
                elif pat == "VALUE_TRAP_WARNING" and g["grade"] in ("A", "B"):
                    sm_notes.append("VALUE_TRAP_WARNING pattern caps at C")
                    g["grade"] = "C"
                elif pat == "SHORT_SQUEEZE_SETUP":
                    # Spec: do NOT auto-elevate; mark with opportunistic tag.
                    opportunistic_tag = True
                    sm_notes.append("SHORT_SQUEEZE_SETUP (opportunistic tag, no elevation)")
            elif informational and pat and pat != "MIXED_SIGNALS":
                sm_notes.append(f"smart-money {pat} (LOW confidence, informational only)")
        if sm_notes:
            g["reason"] = g["reason"] + "  ||  " + "; ".join(sm_notes)
        g["opportunistic"] = opportunistic_tag
        g["initial_grade"] = g["grade"]   # before count caps
        graded.append(g)

    # ── Apply hard count limits ──────────────────────────────────────────────
    graded.sort(key=lambda x: x["score"], reverse=True)
    counts = Counter()
    LIMITS = {"A": MAX_A, "B": MAX_B, "C": MAX_C}
    for g in graded:
        ig = g["initial_grade"]
        if ig is None:
            g["final_grade"] = None
            continue
        if counts[ig] < LIMITS[ig]:
            g["final_grade"] = ig
            counts[ig] += 1
        else:
            # Spec says "force selectivity" -- excess is dropped from the brief.
            g["final_grade"] = None
            g["reason"] = (f"Dropped: would have been {ig} (score {g['score']}) "
                            f"but tier already full at {LIMITS[ig]}; "
                            + g["reason"].split(": ", 1)[-1])

    # ── Persist ───────────────────────────────────────────────────────────────
    updated = 0
    for g in graded:
        payload = {
            "conviction_grade":          g["final_grade"],
            "conviction_score_adjusted": g["score"],
            "grade_reasoning":           g["reason"],
        }
        try:
            db.table("ranked_focus_list").update(payload) \
              .eq("run_date", run_date).eq("ticker", g["ticker"]) \
              .eq("run_type", run_type).execute()
            updated += 1
        except Exception as exc:
            log.debug("update %s failed: %s", g["ticker"], exc)
    log.info("ranked_focus_list: v2 grade columns written for %d/%d",
             updated, len(graded))
    if updated == 0:
        log.warning("Nothing persisted -- apply migration 025_conviction_v2_columns.sql")

    _report(graded)


# ══════════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════════

def _report(graded) -> None:
    bar = "=" * 78
    dist = Counter(g["final_grade"] for g in graded)
    A, B, C = dist.get("A", 0), dist.get("B", 0), dist.get("C", 0)
    dropped = sum(1 for g in graded if g["final_grade"] is None)

    print(f"\n{bar}\nCONVICTION GRADER v2 -- {len(graded)} picks\n{bar}")
    print(f"\n[ GRADE DISTRIBUTION ]")
    print(f"  A: {A}   B: {B}   C: {C}   dropped: {dropped}   "
          f"(caps {MAX_A}/{MAX_B}/{MAX_C})")

    a_picks = [g for g in graded if g["final_grade"] == "A"]
    print(f"\n[ A-GRADE PICKS  --  {len(a_picks)} ]")
    if not a_picks:
        print("  (none qualified -- need score >= 75 AND critic == NONE)")
    for g in a_picks:
        print(f"\n  A   {g['ticker']:<6}  score {g['score']:>5.1f}  "
              f"(base {g['base_score']:.1f})")
        print(f"      {g['reason']}")

    critic_downgrades = [g for g in graded
                          if g["critic_caps_c"] and g["base_score"] >= THRESH_B]
    print(f"\n[ DOWNGRADED BY CRITIC STRONG OBJECTION  --  {len(critic_downgrades)} ]")
    if not critic_downgrades:
        print("  (no high-scoring picks were capped at C by the critic)")
    for g in critic_downgrades:
        would = ("A (>=75)" if g["base_score"] >= THRESH_A
                 else "B (>=55)")
        print(f"  {g['ticker']:<6}  base {g['base_score']:>5.1f}  would have been "
              f"{would}  ->  C (critic STRONG)")
        print(f"        {g['reason']}")

    tier_overflow = [g for g in graded
                      if g["initial_grade"] is not None
                      and g["final_grade"] != g["initial_grade"]
                      and not g["critic_caps_c"]]
    if tier_overflow:
        print(f"\n[ DROPPED BY COUNT CAP  --  {len(tier_overflow)} ]")
        for g in tier_overflow:
            print(f"  {g['ticker']:<6}  would have been {g['initial_grade']} "
                  f"(score {g['score']:.1f}) -- tier full")
    print(f"\n{bar}\n")


# ══════════════════════════════════════════════════════════════════════════════
# Peer-pass: rerun adjustments AFTER peer_industry_analysis has populated
# the 3D verdict. Reads peer_industry_analysis and rewrites conviction_grade
# + appends to grade_reasoning per Step 6.5 spec.
#
# Rules:
#   1) industry_stance=='underweight' AND best_in_industry != target
#         -> downgrade one full grade (A->B, B->C, C->dropped)
#   2) industry_stance=='overweight'  AND best_in_industry == target
#         -> boost conviction_score_adjusted by 10% (re-grade afterwards)
#   3) best_in_industry != target AND grade was 'A'
#         -> append "Note: <peer> ranks higher on peer composite" to reasoning
#   4) disruption_risk_score > 70 -> cap grade at B
#   5) best_in_industry beats target by > 15 pts -> already handled by
#      peer_industry_analysis.py via auto_added_watchlist
# ══════════════════════════════════════════════════════════════════════════════

_ORDER = ["A", "B", "C"]


def _demote(grade):
    if grade not in _ORDER:
        return None
    i = _ORDER.index(grade)
    return _ORDER[i + 1] if i + 1 < len(_ORDER) else None


def _regrade_from_score(score, critic):
    """Re-derive grade from an adjusted score using the same thresholds."""
    if critic == "STRONG":
        return "C"
    if score >= THRESH_A and critic == "NONE":
        return "A"
    if score >= THRESH_B:
        return "B"
    if score >= THRESH_C:
        return "C"
    return None


def apply_peer_adjustments(run_type: str = "midday") -> None:
    if run_type not in ("premarket", "midday", "close"):
        run_type = "midday"
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Conviction Grader v2 -- PEER PASS -- run_type=%s", run_type)

    recent = (db.table("ranked_focus_list").select("run_date")
              .order("run_date", desc=True).limit(1).execute().data)
    if not recent:
        log.warning("ranked_focus_list empty.")
        return
    run_date = recent[0]["run_date"]

    pia_rows = (db.table("peer_industry_analysis")
                .select("target_ticker,industry_stance,best_in_industry_ticker,"
                        "best_in_industry_is_target,disruption_risk_score,"
                        "overall_peer_score,investability_score,peer_comparison")
                .eq("snapshot_date", run_date).eq("run_type", run_type)
                .execute().data or [])
    if not pia_rows:
        log.warning("No peer_industry_analysis rows -- run peer_industry_analysis first.")
        return

    log.info("Loaded %d peer/industry verdict(s)", len(pia_rows))
    changes = []

    for pia in pia_rows:
        t = pia["target_ticker"]
        pick = (db.table("ranked_focus_list")
                .select("conviction_grade,conviction_score_adjusted,"
                        "grade_reasoning,critic_objection_level")
                .eq("run_date", run_date).eq("ticker", t)
                .eq("run_type", run_type).limit(1).execute().data or [])
        if not pick:
            continue
        pick = pick[0]
        old_grade   = pick.get("conviction_grade")
        old_score   = _num(pick.get("conviction_score_adjusted")) or 0.0
        old_reason  = pick.get("grade_reasoning") or ""
        critic      = _normalize_critic(pick.get("critic_objection_level"))

        new_grade   = old_grade
        new_score   = old_score
        notes       = []
        stance      = pia.get("industry_stance")
        is_best     = bool(pia.get("best_in_industry_is_target"))
        best_t      = pia.get("best_in_industry_ticker")
        disruption  = _num(pia.get("disruption_risk_score")) or 0.0

        # Rule 1: weak industry + not the leader -> demote one grade.
        if stance == "underweight" and not is_best:
            demoted = _demote(new_grade) if new_grade else None
            if demoted != new_grade:
                notes.append(f"Demoted: weak industry + not industry leader")
                new_grade = demoted

        # Rule 2: strong industry + is the leader -> +10% score, re-grade.
        if stance == "overweight" and is_best:
            new_score = round(new_score * 1.10, 2)
            new_grade = _regrade_from_score(new_score, critic)
            notes.append(f"Boosted: strong industry + industry leader (+10%)")

        # Rule 3: A-grade but a peer ranks higher.
        if old_grade == "A" and not is_best and best_t:
            # The best's overall_peer_score lives inside peer_comparison.overall_score.
            best_score = None
            pc = pia.get("peer_comparison") or {}
            os_map = pc.get("overall_score") or {}
            best_score = _num(os_map.get(best_t))
            target_score = _num(pia.get("overall_peer_score")) or 0.0
            best_s = f"{best_score:.1f}" if best_score is not None else "?"
            notes.append(
                f"Note: {best_t} ranks higher on peer composite "
                f"({best_s} vs target {target_score:.1f}) -- consider as alternative"
            )

        # Rule 4: high disruption risk caps at B.
        if disruption > 70 and new_grade == "A":
            new_grade = "B"
            notes.append(f"Capped at B: industry disruption risk {disruption:.0f}/100")

        if notes:
            new_reason = old_reason + "  ||  " + "; ".join(notes)
            try:
                db.table("ranked_focus_list").update({
                    "conviction_grade":          new_grade,
                    "conviction_score_adjusted": new_score,
                    "grade_reasoning":           new_reason,
                }).eq("run_date", run_date).eq("ticker", t) \
                  .eq("run_type", run_type).execute()
                changes.append({
                    "ticker": t, "from": old_grade, "to": new_grade,
                    "score_from": old_score, "score_to": new_score,
                    "notes": notes,
                })
            except Exception as exc:
                log.warning("update %s failed: %s", t, exc)

    # Report
    bar = "=" * 78
    print(f"\n{bar}\nCONVICTION GRADER v2 -- PEER PASS -- {len(pia_rows)} A-pick(s)\n{bar}")
    if not changes:
        print("\n  No grade or score changes from peer/industry verdict.")
    else:
        print(f"\n[ ADJUSTMENTS APPLIED  --  {len(changes)} ]")
        for c in changes:
            direction = "->"
            print(f"\n  {c['ticker']:<6}  {c['from']} {direction} {c['to']}"
                  f"   score {c['score_from']:.1f} {direction} {c['score_to']:.1f}")
            for n in c["notes"]:
                print(f"      - {n}")
    print(f"\n{bar}\n")


if __name__ == "__main__":
    arg  = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    mode = sys.argv[2].lower() if len(sys.argv) > 2 else "primary"
    if mode in ("peer", "peer-pass", "peer_pass"):
        apply_peer_adjustments(arg)
    else:
        run(arg)
