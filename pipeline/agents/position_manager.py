"""
Position Manager  (Step 6.8)
Continuous monitoring of EXISTING portfolio positions -- the underserved
half of advisor work. For every (portfolio, holding) we compute six
assessments:

  1. thesis_degradation -- original reason for owning is no longer valid
                            (entry grade dropped, industry stance flipped,
                            sentiment turned, smart money disagrees)
  2. profit_taking      -- asymmetric gain captured; risk/reward inverted
                            (unrealized > 25% + analyst targets hit, IV high,
                            RSI sustained > 80, DCF upside exhausted)
  3. stop_loss          -- approach or breach of technical / fundamental /
                            time stops
  4. time_review        -- > 180d held without thesis refresh (stale),
                            or > 365d flat ("dead money")
  5. better_alternative -- peer now has higher conviction; "consider swap"
  6. no_action          -- nothing flagged; the default

A synthesis layer picks the dominant signal per holding (sorted by strength).
Groq writes the per-position reasoning under a STRICT closed-context prompt
with the same research-not-advice discipline as smart_money_intel:
NEVER "sell", "must", "recommend you" -- only "review", "consider",
"thesis weakening", "data suggests".

A separate portfolio_level_analysis() aggregates across holdings:
overweight / underweight / sector concentration / factor drift / cash
deployment opportunity / top-3 advisor priorities.

ETHICAL FRAMING
  This is RESEARCH for a licensed advisor, not advice to a retail investor.
  Every reasoning string is scanned for forbidden tokens before write. Any
  match -> the structured signal still persists but `reasoning` is set to a
  fallback template (so the row never silently ships banned language).

Usage:
    python pipeline/agents/position_manager.py
        Runs the analysis for every portfolio in the database.

    python pipeline/agents/position_manager.py <portfolio_id>
        Runs for one portfolio by UUID.

    python pipeline/agents/position_manager.py --demo
        Runs for the "Fortis Demo Portfolio" only.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (  # noqa: E402
    GROQ_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL,
)
from supabase import create_client  # noqa: E402

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_TEMPERATURE = 0.3

# Forbidden language -- same gate as smart_money_intel. The agent will fall
# back to a template if the LLM violates this.
FORBIDDEN_PATTERNS = [
    re.compile(r"\b(?:sell|sells|selling|sold)\b", re.IGNORECASE),
    re.compile(r"\bmust\b", re.IGNORECASE),
    re.compile(r"\brecommend\s+you\b", re.IGNORECASE),
    re.compile(r"\bexit\s+(?:now|the\s+position|immediately)\b", re.IGNORECASE),
]

# Signal-strength weights for synthesis (highest strength wins; ties broken
# by this priority order so "stop_loss approach" beats a "time_review" with
# the same numeric strength).
SIGNAL_PRIORITY = {
    "stop_loss":          5,
    "thesis_degradation": 4,
    "profit_taking":      3,
    "better_alternative": 2,
    "time_review":        1,
    "no_action":          0,
}

# Default rebalance thresholds. Real advisors can configure these per-portfolio
# later; today these are sensible defaults.
DEFAULT_TARGET_WEIGHT_PCT = None      # equal-weight is implied when None
SECTOR_OVERWEIGHT_THRESHOLD = 0.35    # any sector > 35% triggers concentration
SINGLE_NAME_OVERWEIGHT_THRESHOLD = 0.15  # any single ticker > 15%
DEAD_MONEY_PCT_BAND = 0.05            # ±5% over 365d = dead money
THESIS_STALE_DAYS = 180

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _to_date(d):
    if d is None:
        return None
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    try:
        return datetime.fromisoformat(str(d)[:10]).date()
    except Exception:
        return None


def _fetch_all(query_fn, page=1000):
    rows, start = [], 0
    while True:
        chunk = query_fn().range(start, start + page - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        start += page


def _current_prices(tickers: list[str]) -> dict[str, float]:
    """Latest close for a list of tickers (yfinance, single batch call)."""
    if not tickers:
        return {}
    try:
        raw = yf.download(tickers, period="5d", interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker",
                          threads=True)
    except Exception as exc:
        log.warning("yfinance batch for current prices failed: %s", exc)
        return {}
    out = {}
    multi = hasattr(raw, "columns") and getattr(raw.columns, "nlevels", 1) > 1
    for t in tickers:
        try:
            df = (raw[t] if multi else raw).dropna(how="all")
            if df.empty:
                continue
            out[t] = float(df["Close"].iloc[-1])
        except Exception:
            continue
    return out


def _scan_forbidden(text: str) -> list[str]:
    if not text:
        return []
    hits = []
    for pat in FORBIDDEN_PATTERNS:
        for m in pat.finditer(text):
            hits.append(m.group(0))
    return hits


# ══════════════════════════════════════════════════════════════════════════════
# Context loaders -- one DB pass per source, keyed by ticker for cheap lookup
# ══════════════════════════════════════════════════════════════════════════════

def _load_context(db, tickers: list[str], today: date) -> dict:
    """Pre-loads everything any assessment method might need, keyed by ticker."""
    ctx: dict = {"tickers": tickers, "today": today}

    # Latest ranked_focus_list row per ticker (any run_type).
    rfl_rows = _fetch_all(lambda: db.table("ranked_focus_list")
                          .select("ticker,run_date,run_type,rank,composite_score,"
                                  "conviction_grade,conviction_score_adjusted,"
                                  "grade_reasoning,catalyst_category,"
                                  "catalyst_description,critic_objection_level,"
                                  "bull_case,bear_case")
                          .in_("ticker", tickers)
                          .order("run_date", desc=True))
    rfl = {}
    for r in rfl_rows:
        rfl.setdefault(r["ticker"], r)   # latest wins because of order desc
    ctx["rfl"] = rfl

    # Latest market snapshot per ticker
    ms_rows = _fetch_all(lambda: db.table("market_snapshots")
                         .select("ticker,price,snapshot_time,gap_pct,"
                                 "relative_volume,moving_averages,momentum,"
                                 "volatility,price_levels,support_resistance")
                         .in_("ticker", tickers)
                         .order("snapshot_time", desc=True))
    ms = {}
    for r in ms_rows:
        ms.setdefault(r["ticker"], r)
    ctx["market"] = ms

    # Pick outcomes (joins forward returns + smart_money_pattern at pick time)
    po_rows = _fetch_all(lambda: db.table("pick_outcomes")
                         .select("ticker,recommended_date,recommended_run_type,"
                                 "conviction_grade,composite_score,entry_price,"
                                 "smart_money_pattern,smart_money_confidence,"
                                 "composite_smart_money_score")
                         .in_("ticker", tickers)
                         .order("recommended_date", desc=True))
    po = {}
    for r in po_rows:
        po.setdefault(r["ticker"], r)
    ctx["entry_history"] = po

    # Latest smart_money_intel
    try:
        sm_rows = _fetch_all(lambda: db.table("smart_money_intel")
                             .select("ticker,pattern_detected,confidence_level,"
                                     "composite_smart_money_score,"
                                     "insider_signal_score,short_signal_score,"
                                     "snapshot_date")
                             .in_("ticker", tickers)
                             .order("snapshot_date", desc=True))
        sm = {}
        for r in sm_rows:
            sm.setdefault(r["ticker"], r)
        ctx["smart_money"] = sm
    except Exception:
        ctx["smart_money"] = {}

    # Latest sentiment per ticker
    try:
        sent_rows = _fetch_all(lambda: db.table("ticker_sentiment_rollup")
                               .select("ticker,weighted_sentiment,snapshot_date")
                               .in_("ticker", tickers)
                               .order("snapshot_date", desc=True))
        sent = {}
        for r in sent_rows:
            sent.setdefault(r["ticker"], r)
        ctx["sentiment"] = sent
    except Exception:
        ctx["sentiment"] = {}

    # Peer/industry analysis -- for better_alternative checks
    try:
        pia_rows = _fetch_all(lambda: db.table("peer_industry_analysis")
                              .select("target_ticker,industry_stance,"
                                      "best_in_industry_ticker,"
                                      "best_in_industry_is_target,"
                                      "disruption_risk_score,"
                                      "overall_peer_score,snapshot_date")
                              .in_("target_ticker", tickers)
                              .order("snapshot_date", desc=True))
        pia = {}
        for r in pia_rows:
            pia.setdefault(r["target_ticker"], r)
        ctx["peer"] = pia
    except Exception:
        ctx["peer"] = {}

    # Factor exposure for sector/factor concentration
    try:
        fx_rows = _fetch_all(lambda: db.table("factor_exposure")
                             .select("ticker,sector,factor_tags,snapshot_date")
                             .in_("ticker", tickers)
                             .order("snapshot_date", desc=True))
        fx = {}
        for r in fx_rows:
            fx.setdefault(r["ticker"], r)
        ctx["factor"] = fx
    except Exception:
        ctx["factor"] = {}

    return ctx


# ══════════════════════════════════════════════════════════════════════════════
# Per-holding assessments. Each returns a dict; later we pick the strongest.
# ══════════════════════════════════════════════════════════════════════════════

def _empty_signal(stype: str) -> dict:
    return {
        "signal_type":                  stype,
        "signal_strength":              0,
        "recommended_action":           "hold",
        "recommended_size_change_pct":  0,
        "supporting_data":              {},
        "requires_immediate_attention": False,
    }


def assess_thesis_degradation(holding, ctx) -> dict:
    """Has the original reason for owning weakened?"""
    t = holding["ticker"]
    out = _empty_signal("thesis_degradation")
    rfl = ctx["rfl"].get(t)
    entry = ctx["entry_history"].get(t)
    sm = ctx["smart_money"].get(t) or {}
    peer = ctx["peer"].get(t) or {}
    sent = ctx["sentiment"].get(t) or {}

    reasons = []
    strength = 0

    # 1. Conviction grade drop. If we have an entry grade (pick_outcomes) and
    # today's grade is worse, that's degradation.
    entry_grade = (entry or {}).get("conviction_grade")
    today_grade = (rfl or {}).get("conviction_grade")
    grade_rank = {"A": 3, "B": 2, "C": 1}
    if entry_grade in grade_rank and today_grade in grade_rank:
        if grade_rank[today_grade] < grade_rank[entry_grade]:
            steps = grade_rank[entry_grade] - grade_rank[today_grade]
            strength += 25 * steps
            reasons.append(f"conviction grade dropped {entry_grade}->{today_grade}")
    elif entry_grade in grade_rank and today_grade is None:
        # Ticker no longer makes the brief at all
        strength += 35
        reasons.append(f"no longer on focus list (entry grade {entry_grade})")

    # 2. Critic objection escalation
    critic = (rfl or {}).get("critic_objection_level") or ""
    if str(critic).upper().startswith("STRONG"):
        strength += 20
        reasons.append("critic raised STRONG objection")

    # 3. Industry stance flipped to underweight
    if peer.get("industry_stance") == "underweight":
        strength += 15
        reasons.append("industry stance now underweight")

    # 4. Smart-money pattern is bearish-divergent
    if sm.get("pattern_detected") in ("INSIDER_SMOKE_SIGNAL",
                                       "VALUE_TRAP_WARNING",
                                       "QUIET_DISTRIBUTION",
                                       "FULL_CONVICTION_SHORT"):
        strength += 25
        reasons.append(f"smart money pattern {sm['pattern_detected']}")

    # 5. Sentiment turned net negative
    ws = _num(sent.get("weighted_sentiment"))
    if ws is not None and ws < -0.25:
        strength += 10
        reasons.append(f"weighted sentiment {ws:+.2f}")

    if strength == 0:
        return out
    out["signal_strength"] = min(100, strength)
    out["recommended_action"] = (
        "consider_exit" if strength >= 60
        else "consider_trim" if strength >= 35
        else "review_position")
    out["recommended_size_change_pct"] = (
        -50 if strength >= 60 else -25 if strength >= 35 else 0)
    out["supporting_data"] = {
        "entry_grade":       entry_grade,
        "today_grade":       today_grade,
        "critic":            critic,
        "industry_stance":   peer.get("industry_stance"),
        "smart_money":       sm.get("pattern_detected"),
        "smart_money_conf":  sm.get("confidence_level"),
        "weighted_sentiment": ws,
        "reasons":           reasons,
    }
    out["requires_immediate_attention"] = strength >= 60
    return out


def assess_profit_taking(holding, ctx, unrealized_pct, current_price) -> dict:
    """If unrealized > 25%, has risk/reward inverted?"""
    out = _empty_signal("profit_taking")
    if unrealized_pct is None or unrealized_pct < 25:
        return out

    t = holding["ticker"]
    m = ctx["market"].get(t) or {}
    momentum = m.get("momentum") or {}
    volatility = m.get("volatility") or {}
    price_levels = m.get("price_levels") or {}

    reasons = []
    strength = 30   # base just for being up > 25%
    reasons.append(f"position up {unrealized_pct:.1f}% from cost basis")

    rsi = _num((momentum or {}).get("rsi_14") or (momentum or {}).get("rsi"))
    if rsi is not None and rsi >= 80:
        strength += 25
        reasons.append(f"RSI {rsi:.0f} in extreme overbought")
    elif rsi is not None and rsi >= 75:
        strength += 12
        reasons.append(f"RSI {rsi:.0f} elevated")

    # 30d realized vol -- proxy for IV spike
    rv = _num((volatility or {}).get("realized_vol_30d"))
    if rv is not None and rv > 0.50:
        strength += 10
        reasons.append(f"30d realized vol {rv:.2f} -- expansion")

    # Approach to 52-week high (within 2%)
    high_52w = _num((price_levels or {}).get("high_52w"))
    if high_52w and current_price and high_52w > 0:
        gap = (high_52w - current_price) / high_52w
        if 0 <= gap <= 0.02:
            strength += 10
            reasons.append(f"price within {gap*100:.1f}% of 52-week high")

    # Strong gains threshold
    if unrealized_pct >= 60:
        strength += 15
        reasons.append("> 60% gain -- meaningful asymmetry captured")

    out["signal_strength"] = min(100, strength)
    out["recommended_action"] = (
        "consider_trim" if strength >= 55 else "review_position")
    # Trim 33-50% as profit-taking suggestion, never full exit on this path
    out["recommended_size_change_pct"] = (
        -50 if strength >= 75 else -33 if strength >= 55 else -20)
    out["supporting_data"] = {
        "unrealized_pct":   unrealized_pct,
        "rsi_14":           rsi,
        "realized_vol_30d": rv,
        "high_52w":         high_52w,
        "current_price":    current_price,
        "reasons":          reasons,
    }
    out["requires_immediate_attention"] = strength >= 75
    return out


def assess_stop_loss(holding, ctx, current_price, unrealized_pct) -> dict:
    """Technical / fundamental / time stop approach or breach."""
    out = _empty_signal("stop_loss")
    if current_price is None:
        return out

    t = holding["ticker"]
    m = ctx["market"].get(t) or {}
    ma = m.get("moving_averages") or {}
    sr = m.get("support_resistance") or {}
    pl = m.get("price_levels") or {}

    reasons = []
    strength = 0

    # Technical: price has broken below 200-day MA
    sma_200 = _num((ma or {}).get("sma_200"))
    if sma_200 and current_price < sma_200:
        below_pct = (sma_200 - current_price) / sma_200 * 100
        strength += min(40, 15 + int(below_pct * 2))
        reasons.append(f"price {below_pct:.1f}% below 200-day MA")

    # Approach to nearest support (within 3%)
    support = _num((sr or {}).get("nearest_support"))
    if support and current_price and support > 0:
        gap = (current_price - support) / support
        if -0.005 < gap < 0.03:
            strength += 25
            reasons.append(f"price within {gap*100:.1f}% of nearest support")

    # 52-week low approach
    low_52w = _num((pl or {}).get("low_52w"))
    if low_52w and current_price and low_52w > 0:
        gap = (current_price - low_52w) / low_52w
        if gap < 0.05:
            strength += 25
            reasons.append(f"price within {gap*100:.1f}% of 52-week low")

    # Fundamental stop: thesis broken -- proxied by today's conviction grade
    rfl = ctx["rfl"].get(t) or {}
    grade = rfl.get("conviction_grade")
    if grade == "C":
        strength += 15
        reasons.append("conviction grade now C")
    elif grade is None and unrealized_pct is not None and unrealized_pct < -10:
        strength += 20
        reasons.append("dropped from focus list while down > 10%")

    # Position is meaningfully underwater -- amplifies all of the above
    if unrealized_pct is not None and unrealized_pct < -15:
        strength = min(100, strength + 15)
        reasons.append(f"position down {unrealized_pct:.1f}% from cost basis")

    if strength == 0:
        return out
    out["signal_strength"] = min(100, strength)
    out["recommended_action"] = (
        "consider_exit" if strength >= 70
        else "consider_trim" if strength >= 45 else "review_position")
    out["recommended_size_change_pct"] = (
        -100 if strength >= 80 else -50 if strength >= 60
        else -25 if strength >= 40 else 0)
    out["supporting_data"] = {
        "current_price":   current_price,
        "sma_200":         sma_200,
        "nearest_support": support,
        "low_52w":         low_52w,
        "today_grade":     grade,
        "unrealized_pct":  unrealized_pct,
        "reasons":         reasons,
    }
    out["requires_immediate_attention"] = strength >= 60
    return out


def assess_time_based_review(holding, ctx, holding_days, unrealized_pct) -> dict:
    """Stale thesis (>180d) or dead money (>365d flat)."""
    out = _empty_signal("time_review")
    if holding_days is None:
        return out
    reasons = []
    strength = 0
    if holding_days > 365 and unrealized_pct is not None \
            and abs(unrealized_pct) <= DEAD_MONEY_PCT_BAND * 100:
        strength = 55
        reasons.append(f"{holding_days}d held, return {unrealized_pct:+.1f}% "
                        "-- dead money")
        out["recommended_action"] = "review_position"
        out["recommended_size_change_pct"] = -50
        out["requires_immediate_attention"] = True
    elif holding_days > THESIS_STALE_DAYS:
        strength = 30
        reasons.append(f"{holding_days}d held without recent thesis update")
        out["recommended_action"] = "review_position"
        out["recommended_size_change_pct"] = 0

    if strength == 0:
        return out
    out["signal_strength"] = strength
    out["supporting_data"] = {
        "holding_period_days": holding_days,
        "unrealized_pct":      unrealized_pct,
        "reasons":             reasons,
    }
    return out


def assess_better_alternatives(holding, ctx) -> dict:
    """Is there a peer with materially better conviction?"""
    t = holding["ticker"]
    out = _empty_signal("better_alternative")
    peer = ctx["peer"].get(t) or {}
    best = peer.get("best_in_industry_ticker")
    is_best = bool(peer.get("best_in_industry_is_target"))
    if not best or is_best or best == t:
        return out

    # Compare conviction grades
    holding_rfl = ctx["rfl"].get(t) or {}
    holding_grade = holding_rfl.get("conviction_grade")
    grade_rank = {"A": 3, "B": 2, "C": 1, None: 0}
    # We don't have the peer's grade pre-loaded; fetch via a single point lookup
    # only if the holding grade isn't already A.
    if holding_grade == "A":
        return out

    reasons = [f"peer {best} ranks higher on industry composite"]
    strength = 25
    disruption = _num(peer.get("disruption_risk_score")) or 0
    if disruption > 60:
        strength += 15
        reasons.append(f"industry disruption risk {disruption:.0f}/100")

    if peer.get("industry_stance") == "underweight":
        strength += 15
        reasons.append("industry stance underweight")

    out["signal_strength"] = min(100, strength)
    out["recommended_action"] = "review_position"
    out["recommended_size_change_pct"] = 0
    out["supporting_data"] = {
        "alternative_ticker": best,
        "current_grade":      holding_grade,
        "disruption_risk":    disruption,
        "industry_stance":    peer.get("industry_stance"),
        "reasons":            reasons,
    }
    out["requires_immediate_attention"] = strength >= 55
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Synthesis + Groq reasoning
# ══════════════════════════════════════════════════════════════════════════════

def _pick_dominant(signals: list[dict]) -> dict:
    real = [s for s in signals if s["signal_strength"] > 0]
    if not real:
        return _empty_signal("no_action")
    real.sort(key=lambda s: (s["signal_strength"],
                             SIGNAL_PRIORITY.get(s["signal_type"], 0)),
              reverse=True)
    return real[0]


_REASONING_SYSTEM = (
    "You are a senior portfolio analyst writing a position-management note "
    "for a licensed advisor. Use language appropriate for a research alert, "
    "NOT a recommendation. Use phrases like 'data suggests reviewing', "
    "'thesis signals weakening', 'consider whether to trim', 'review-worthy'. "
    "NEVER use the words 'sell', 'must', or 'recommend you'. NEVER write "
    "'exit now' or 'exit the position'. This is research for a licensed "
    "advisor, not advice to a retail investor. Keep the note to 3-4 sentences."
)


def _build_reasoning_prompt(holding, signal, current_price, unrealized_pct):
    sd = signal.get("supporting_data") or {}
    reasons = sd.get("reasons") or []
    return (
        f"Holding: {holding['ticker']} (held {sd.get('holding_period_days', '?')} days). "
        f"Cost basis: ${holding.get('cost_basis')}.  Current price: ${current_price}.  "
        f"Unrealized return: {unrealized_pct:+.1f}%.\n"
        f"Detected signal: {signal['signal_type']} "
        f"(strength {signal['signal_strength']}/100).\n"
        f"Supporting findings:\n" + "\n".join(f"- {r}" for r in reasons) +
        "\n\nWrite a 3-4 sentence position-review note for the advisor's brief. "
        "Frame it as research, not a directive. End with one explicit "
        "'For advisor review -- not a recommendation.' line."
    )


def _groq(prompt: str) -> str | None:
    if not GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": _REASONING_SYSTEM},
                      {"role": "user", "content": prompt}],
            temperature=GROQ_TEMPERATURE, max_tokens=350)
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as exc:
        log.warning("Groq position reasoning failed: %s", exc)
        return None


_TEMPLATE_REASONING = {
    "thesis_degradation":
        "Data suggests reviewing this position. {reasons_short} For advisor "
        "review -- not a recommendation.",
    "profit_taking":
        "Position has captured meaningful asymmetric gain ({unrealized:+.1f}%). "
        "{reasons_short} Consider whether to trim back to target weight. For "
        "advisor review -- not a recommendation.",
    "stop_loss":
        "Technical and/or fundamental stop indicators have triggered. "
        "{reasons_short} Review-worthy. For advisor review -- not a "
        "recommendation.",
    "time_review":
        "{reasons_short} Consider whether the original thesis still holds. "
        "For advisor review -- not a recommendation.",
    "better_alternative":
        "Peer-comparison data suggests an industry peer currently scores "
        "higher on the composite. {reasons_short} Consider whether the "
        "current allocation reflects the relative read. For advisor review "
        "-- not a recommendation.",
    "no_action":
        "No material exit signal detected at this snapshot. Position remains "
        "within current thesis parameters.",
}


def generate_position_reasoning(holding, signal, current_price,
                                 unrealized_pct) -> tuple[str, bool]:
    """Returns (reasoning_text, used_fallback). The forbidden-language gate
    forces the template if the LLM output violates."""
    if signal["signal_type"] == "no_action":
        return _TEMPLATE_REASONING["no_action"], False
    prompt = _build_reasoning_prompt(holding, signal, current_price,
                                      unrealized_pct)
    text = _groq(prompt)
    if text:
        hits = _scan_forbidden(text)
        if hits:
            log.warning("%s: position reasoning used forbidden language %s -- "
                        "falling back to template", holding["ticker"], hits)
            text = None
    if text:
        return text, False

    # Template fallback
    reasons = (signal.get("supporting_data") or {}).get("reasons") or []
    reasons_short = "; ".join(reasons[:3]) + "." if reasons else ""
    template = _TEMPLATE_REASONING.get(signal["signal_type"],
                                         _TEMPLATE_REASONING["no_action"])
    return template.format(reasons_short=reasons_short,
                            unrealized=unrealized_pct or 0.0), True


# ══════════════════════════════════════════════════════════════════════════════
# Per-holding orchestration
# ══════════════════════════════════════════════════════════════════════════════

def analyze_holding(db, portfolio_id, holding, ctx, today, run_type):
    t = holding["ticker"]
    cost_basis = _num(holding.get("cost_basis"))
    current_price = _num((ctx["market"].get(t) or {}).get("price"))
    # If we don't have a recent market snapshot, fall back to yfinance.
    if current_price is None:
        current_price = ctx["fallback_prices"].get(t)

    if cost_basis is None or current_price is None:
        log.debug("%s: missing cost_basis or current_price -- skipping", t)
        return None

    unrealized_pct = round((current_price - cost_basis) / cost_basis * 100, 2)
    added = holding.get("added_at")
    if isinstance(added, str):
        try:
            added_dt = datetime.fromisoformat(added.replace("Z", "+00:00"))
        except Exception:
            added_dt = None
    else:
        added_dt = added
    holding_days = ((today - added_dt.date()).days
                     if added_dt is not None else None)

    # Run all five assessments
    signals = [
        assess_thesis_degradation(holding, ctx),
        assess_profit_taking(holding, ctx, unrealized_pct, current_price),
        assess_stop_loss(holding, ctx, current_price, unrealized_pct),
        assess_time_based_review(holding, ctx, holding_days, unrealized_pct),
        assess_better_alternatives(holding, ctx),
    ]
    # Annotate each signal with holding_period_days for the reasoning prompt
    for s in signals:
        s.setdefault("supporting_data", {})["holding_period_days"] = holding_days

    dominant = _pick_dominant(signals)

    reasoning, _ = generate_position_reasoning(holding, dominant,
                                                 current_price, unrealized_pct)

    entry_history = ctx["entry_history"].get(t) or {}
    entry_basis = {
        "entry_grade":      entry_history.get("conviction_grade"),
        "entry_run_date":   str(entry_history.get("recommended_date") or ""),
        "entry_run_type":   entry_history.get("recommended_run_type"),
        "entry_smart_money_pattern": entry_history.get("smart_money_pattern"),
        "added_at":         str(added_dt) if added_dt is not None else None,
        "cost_basis":       cost_basis,
    }

    return {
        "portfolio_id":                  str(portfolio_id),
        "ticker":                        t,
        "snapshot_date":                 today.isoformat(),
        "run_type":                      run_type,
        "entry_basis":                   entry_basis,
        "current_price":                 current_price,
        "cost_basis":                    cost_basis,
        "unrealized_return_pct":         unrealized_pct,
        "holding_period_days":           holding_days,
        "signal_type":                   dominant["signal_type"],
        "signal_strength":               dominant["signal_strength"],
        "recommended_action":            dominant["recommended_action"],
        "recommended_size_change_pct":   dominant["recommended_size_change_pct"],
        "reasoning":                     reasoning,
        "supporting_data":               {
            "all_signals": [
                {k: v for k, v in s.items() if k != "supporting_data"}
                 | {"reasons": (s.get("supporting_data") or {}).get("reasons") or []}
                for s in signals
            ],
            "dominant_signal_detail":    dominant.get("supporting_data") or {},
        },
        "requires_immediate_attention":  dominant["requires_immediate_attention"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Portfolio-level rebalance analysis
# ══════════════════════════════════════════════════════════════════════════════

def portfolio_level_analysis(portfolio_id, holdings, signals,
                              prices, ctx, today, run_type):
    """Aggregate across positions; build the rebalance suggestion row."""
    # Position values
    values = []
    for h in holdings:
        p = prices.get(h["ticker"])
        sh = _num(h.get("shares")) or 0
        if p is not None and sh > 0:
            values.append((h["ticker"], sh * p))
    total = sum(v for _, v in values)
    if total <= 0:
        return None
    weights = {t: v / total for t, v in values}

    target_weight = 1.0 / max(1, len(values))   # equal-weight target

    overweight = []
    underweight = []
    for t, w in weights.items():
        excess = w - target_weight
        if w > SINGLE_NAME_OVERWEIGHT_THRESHOLD or excess > 0.05:
            overweight.append({
                "ticker":         t,
                "weight":         round(w * 100, 2),
                "target_weight":  round(target_weight * 100, 2),
                "excess_pct":     round(excess * 100, 2),
            })
        elif excess < -0.03:
            underweight.append({
                "ticker":         t,
                "weight":         round(w * 100, 2),
                "target_weight":  round(target_weight * 100, 2),
                "excess_pct":     round(excess * 100, 2),
            })
    overweight.sort(key=lambda r: r["excess_pct"], reverse=True)
    underweight.sort(key=lambda r: r["excess_pct"])

    # Concentration alerts -- by sector via factor_exposure
    sector_weights: dict[str, float] = defaultdict(float)
    for t, w in weights.items():
        sector = (ctx["factor"].get(t) or {}).get("sector") or "Unknown"
        sector_weights[sector] += w
    concentration_alerts = []
    for sector, w in sorted(sector_weights.items(), key=lambda x: -x[1]):
        # Skip "Unknown" -- it just means factor_exposure data is missing for
        # those tickers, not that the portfolio is genuinely concentrated.
        if sector == "Unknown":
            continue
        if w > SECTOR_OVERWEIGHT_THRESHOLD:
            concentration_alerts.append({
                "type":        "sector_overweight",
                "scope":       sector,
                "weight":      round(w * 100, 2),
                "threshold":   round(SECTOR_OVERWEIGHT_THRESHOLD * 100, 0),
                "severity":    "HIGH" if w > 0.5 else "MEDIUM",
                "description": f"{sector} = {w*100:.0f}% of portfolio",
            })

    # Single-name concentration
    for t, w in weights.items():
        if w > SINGLE_NAME_OVERWEIGHT_THRESHOLD:
            concentration_alerts.append({
                "type":        "single_name_overweight",
                "scope":       t,
                "weight":      round(w * 100, 2),
                "threshold":   round(SINGLE_NAME_OVERWEIGHT_THRESHOLD * 100, 0),
                "severity":    "HIGH" if w > 0.20 else "MEDIUM",
                "description": f"{t} = {w*100:.0f}% of portfolio",
            })

    # Factor drift (uses factor_tags counts)
    factor_count: Counter = Counter()
    for t, w in weights.items():
        for tag in ((ctx["factor"].get(t) or {}).get("factor_tags") or []):
            factor_count[tag] += w
    factor_drift = [{
        "factor":      tag,
        "weight":      round(w * 100, 2),
        "description": f"{tag} factor = {w*100:.0f}% of portfolio",
    } for tag, w in factor_count.most_common(5) if w > 0.30]

    # Top-3 priorities
    priorities: list[dict] = []
    urgent = [s for s in signals if s["requires_immediate_attention"]]
    # Sort by signal strength descending
    urgent.sort(key=lambda s: s["signal_strength"], reverse=True)
    for s in urgent[:3]:
        priorities.append({
            "action":    s["recommended_action"],
            "ticker":    s["ticker"],
            "signal_type": s["signal_type"],
            "strength":  s["signal_strength"],
            "reasoning": (s.get("reasoning") or "")[:200],
        })
    # Top off with concentration if room
    if len(priorities) < 3 and concentration_alerts:
        for c in concentration_alerts:
            priorities.append({
                "action":    "rebalance_concentration",
                "ticker":    c["scope"],
                "reasoning": c["description"],
                "severity":  c["severity"],
            })
            if len(priorities) >= 3:
                break
    if len(priorities) < 3 and overweight:
        for ow in overweight:
            priorities.append({
                "action":    "consider_trim_to_target",
                "ticker":    ow["ticker"],
                "reasoning": (f"{ow['ticker']} weight {ow['weight']}% vs "
                              f"target {ow['target_weight']}%"),
            })
            if len(priorities) >= 3:
                break

    # LLM summary -- short paragraph framed for the report top
    summary_prompt = (
        f"Portfolio (total ${total:,.0f}, {len(values)} positions). "
        f"Overweight: {[ow['ticker'] for ow in overweight[:3]]}. "
        f"Underweight: {[uw['ticker'] for uw in underweight[:3]]}. "
        f"Concentration: {[c['description'] for c in concentration_alerts]}. "
        f"Urgent attention signals: {len(urgent)}. "
        "Write 2 sentences for the top of an advisor's daily brief, framing "
        "the portfolio-level state. Research framing, not directives. End "
        "with 'For advisor review -- not a recommendation.'"
    )
    reasoning = _groq(summary_prompt) or (
        f"Portfolio holds {len(values)} positions totaling ${total:,.0f}. "
        f"{len(urgent)} position(s) require immediate review. "
        "For advisor review -- not a recommendation."
    )
    if _scan_forbidden(reasoning):
        log.warning("portfolio reasoning forbidden -- using template")
        reasoning = (
            f"Portfolio holds {len(values)} positions totaling ${total:,.0f}. "
            f"{len(urgent)} position(s) require immediate review. "
            "For advisor review -- not a recommendation."
        )

    return {
        "portfolio_id":               str(portfolio_id),
        "snapshot_date":              today.isoformat(),
        "run_type":                   run_type,
        "current_total_value":        round(total, 2),
        "overweight_positions":       overweight,
        "underweight_positions":      underweight,
        "concentration_alerts":       concentration_alerts,
        "factor_drift_alerts":        factor_drift,
        "cash_deployment_opportunity": False,   # we don't track cash today
        "top_3_priorities":           priorities[:3],
        "reasoning":                  reasoning,
    }


# ══════════════════════════════════════════════════════════════════════════════
# DB persistence
# ══════════════════════════════════════════════════════════════════════════════

def _db():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _persist_signal(db, row):
    try:
        (db.table("position_signals")
           .upsert(row,
                    on_conflict="portfolio_id,ticker,snapshot_date,run_type,signal_type")
           .execute())
    except Exception as exc:
        log.warning("position_signals upsert %s/%s failed: %s",
                    row.get("portfolio_id"), row.get("ticker"), exc)


def _persist_rebalance(db, row):
    try:
        (db.table("portfolio_rebalance_suggestions")
           .upsert(row,
                    on_conflict="portfolio_id,snapshot_date,run_type")
           .execute())
    except Exception as exc:
        log.warning("portfolio_rebalance upsert %s failed: %s",
                    row.get("portfolio_id"), exc)


# ══════════════════════════════════════════════════════════════════════════════
# Per-portfolio orchestrator
# ══════════════════════════════════════════════════════════════════════════════

def analyze_portfolio(db, portfolio_id, today, run_type) -> dict:
    holdings = (db.table("portfolio_holdings")
                .select("id,portfolio_id,ticker,shares,cost_basis,added_at")
                .eq("portfolio_id", portfolio_id).execute().data or [])
    if not holdings:
        log.warning("portfolio %s has no holdings", portfolio_id)
        return {"signals": [], "rebalance": None}
    tickers = sorted({h["ticker"] for h in holdings})
    log.info("portfolio %s: %d holdings -- %s",
             portfolio_id, len(holdings),
             ", ".join(tickers[:8]) + ("..." if len(tickers) > 8 else ""))

    ctx = _load_context(db, tickers, today)
    fallback_prices = _current_prices(tickers)
    ctx["fallback_prices"] = fallback_prices

    signals = []
    for h in holdings:
        row = analyze_holding(db, portfolio_id, h, ctx, today, run_type)
        if row is None:
            continue
        signals.append(row)
        _persist_signal(db, row)

    # Portfolio-level
    rebalance = portfolio_level_analysis(
        portfolio_id, holdings, signals, fallback_prices, ctx, today, run_type)
    if rebalance is not None:
        _persist_rebalance(db, rebalance)
    return {"signals": signals, "rebalance": rebalance}


# ══════════════════════════════════════════════════════════════════════════════
# Reporting (CLI)
# ══════════════════════════════════════════════════════════════════════════════

def _report(result, portfolio_name):
    bar = "=" * 80
    sigs = result["signals"]
    print(f"\n{bar}\nPOSITION MANAGER -- {portfolio_name} -- "
          f"{len(sigs)} positions analyzed\n{bar}")

    print("\n[ SIGNAL DISTRIBUTION ]")
    for stype, n in Counter(s["signal_type"] for s in sigs).most_common():
        print(f"  {stype:<22}{n}")

    urgent = [s for s in sigs if s["requires_immediate_attention"]]
    print(f"\n[ URGENT REVIEW -- {len(urgent)} ]")
    for s in sorted(urgent, key=lambda x: x["signal_strength"], reverse=True):
        print(f"  {s['ticker']:<6}  {s['signal_type']:<22} "
              f"strength={s['signal_strength']:>3}  "
              f"action={s['recommended_action']:<18}  "
              f"size_change={s['recommended_size_change_pct']:>4}%")
        print(f"        {(s.get('reasoning') or '')[:300]}")

    rb = result.get("rebalance")
    if rb:
        print(f"\n[ PORTFOLIO-LEVEL TOP-3 PRIORITIES ]")
        for p in rb.get("top_3_priorities", []):
            print(f"  - {p.get('action')}: {p.get('ticker')} -- "
                  f"{(p.get('reasoning') or '')[:200]}")
        if rb.get("concentration_alerts"):
            print(f"\n[ CONCENTRATION ALERTS ]")
            for c in rb["concentration_alerts"]:
                print(f"  {c['severity']:<7}{c['type']:<28}{c['description']}")

    # Quality gate sanity check
    print("\n[ LANGUAGE QUALITY GATE ]")
    bad = []
    for s in sigs:
        hits = _scan_forbidden(s.get("reasoning") or "")
        if hits:
            bad.append((s["ticker"], hits))
    if rb:
        rb_hits = _scan_forbidden(rb.get("reasoning") or "")
        if rb_hits:
            bad.append(("PORTFOLIO_REASONING", rb_hits))
    if bad:
        for t, h in bad:
            print(f"  !! {t}: forbidden language {h}")
    else:
        print("  All reasoning text passed forbidden-language scan.")
    print(f"\n{bar}\n")


def main():
    db = _db()
    today = datetime.now(timezone.utc).date()
    run_type = "midday"
    args = sys.argv[1:]
    if args and args[0] == "--demo":
        rows = (db.table("portfolios").select("id,name")
                .ilike("name", "%demo%").execute().data or [])
        if not rows:
            log.error("no demo portfolio found")
            sys.exit(2)
        portfolio_id = rows[0]["id"]
        result = analyze_portfolio(db, portfolio_id, today, run_type)
        _report(result, rows[0]["name"])
        return
    if args:
        portfolio_id = args[0]
        rows = (db.table("portfolios").select("id,name")
                .eq("id", portfolio_id).execute().data or [])
        name = rows[0]["name"] if rows else portfolio_id
        result = analyze_portfolio(db, portfolio_id, today, run_type)
        _report(result, name)
        return

    # Run for every portfolio
    portfolios = (db.table("portfolios").select("id,name").execute().data or [])
    log.info("Position Manager -- analyzing %d portfolios", len(portfolios))
    for p in portfolios:
        result = analyze_portfolio(db, p["id"], today, run_type)
        _report(result, p["name"])


if __name__ == "__main__":
    main()
