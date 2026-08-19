"""
Stage 3 -- Multibagger Deep Thesis Research
============================================

For each of the top-N scored candidates, generates a structured, BALANCED
thesis via Groq with strict closed-context discipline. Every numeric or
specific claim must be followed by [DATA REF: key], and factcheck_agent
runs over the output before persistence.

System-prompt framing pulls from the engine's philosophy:
  - We are honest about risk -- most candidates fail.
  - The "what kills it" section is as rigorous as the "10x path".
  - Never hype. Never "to the moon", "next Nvidia", "guaranteed".

Data passed to the LLM (one DATA section per ticker):
  - Core metrics from multibagger_candidates  (screener + scorer output)
  - Latest 5 SEC filings    (titles + types + dates)  if present
  - Insider posture summary (form4_transactions)     if present
  - Sector / industry context strings
  - Most recent earnings transcript verdict           if present in
    earnings_transcripts table

Output: a row per (ticker, generated_at) in public.multibagger_theses with
the structured sections, factcheck_score, risk_rating, and conviction_tier.

CLI
    python pipeline/multibagger/deep_research.py            # top 30
    python pipeline/multibagger/deep_research.py --top-n 5  # top 5 only
    python pipeline/multibagger/deep_research.py --tickers ACME,FOO
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.factcheck_agent import factcheck, strip_data_refs  # noqa: E402
from config import GROQ_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402
from llm import complete  # noqa: E402 -- shared provider-waterfall gateway

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

GROQ_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = """You are a small-cap growth analyst who has studied the
traits of historical 100-baggers (small base, long runways, high returns on
capital, owner-operators). You write rigorous, BALANCED multibagger theses.

CRITICAL RULES
- You may ONLY cite facts from the provided DATA section. Every numeric or
  specific claim MUST be immediately followed by [DATA REF: key_name], where
  key_name is one of the snake_case keys in the DATA section.
- ALWAYS write selection_rationale. This name was surfaced by a screen that
  scores six multibagger-DNA traits. In 2-4 sentences, state WHY it was
  shortlisted -- name its highest-scoring traits (the trait_*_score / *_score
  keys) and give the THEORY for why that profile can compound into a
  multibagger. This is MANDATORY and must be written even when external data is
  thin, because the trait scores are always present. It is the heart of the
  dossier -- never leave it generic, never skip it.
- You are honest about risk -- most candidates fail. WHAT_KILLS_IT must be as
  rigorous and specific as THE_10X_PATH.
- Thin data is the NORM for under-covered names. Where a section's external
  data is missing, still give the THEORY from the screen + trait scores, then
  add ONE honest line naming what is not verifiable (e.g. "No analyst coverage
  and few filings -- moat not verifiable from connected sources."). NEVER
  output a bare "DATA section does not provide enough information" as the whole
  section, and NEVER fabricate financials, founders, or a 10x path.
- key_metrics_to_track must be 4 SPECIFIC, human-readable things an analyst
  would watch for THIS thesis (e.g. "quarterly revenue growth holding above the
  33% TTM pace", "operating margin crossing breakeven", "cash runway beyond 18
  months"). NEVER output raw data-key names like "revenue_cagr_3y_pct" -- phrase
  them the way a person would say them, specific to this company.
- Never use hype language. Forbidden: "to the moon", "can't miss",
  "guaranteed", "next Nvidia", "no-brainer", "moonshot", "easy 10x". Plain
  English, no marketing speak.

OUTPUT FORMAT
Return STRICT JSON with these exact keys. No prose outside the JSON.

{
  "selection_rationale":"<MANDATORY: why the screen shortlisted this -- name the top DNA traits (cite the score keys) + the compounding theory; always written even when external data is thin>",
  "business_model": "<plain English: what they do, how they earn revenue>",
  "the_10x_path":   "<concrete path: TAM capture, margin expansion, multiple re-rating; cite metrics>",
  "moat_assessment":"<network effects / switching costs / brand / IP / NONE -- give the theory; if unverifiable from data, say so honestly>",
  "founder_assessment":"<who runs it + alignment from the data; if unknown, say so AND cite the alignment score>",
  "what_has_to_go_right":"<3-4 key assumptions the thesis depends on>",
  "what_kills_it": "<3-4 specific failure modes -- as rigorous as the 10x path>",
  "key_metrics_to_track":["specific readable metric 1","metric 2","metric 3","metric 4"],
  "risk_rating":   "speculative|high_risk|moderate_risk",
  "conviction_tier":"tier_1_high_conviction|tier_2_promising|tier_3_speculative"
}
"""

FORBIDDEN_PHRASES = [
    r"\bto the moon\b",
    r"\bcan'?t miss\b",
    r"\bguaranteed\b",
    r"\bnext nvidia\b",
    r"\bno[- ]brainer\b",
    r"\bmoonshot\b",
    r"\beasy 10x\b",
    r"\b10[- ]bagger lock\b",
]


# ══════════════════════════════════════════════════════════════════════════════
# Data gathering
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_sec_filings(db, ticker: str, limit: int = 5) -> list[dict]:
    try:
        rows = (
            db.table("sec_filings")
              .select("form_type, filing_date, filing_url, description")
              .eq("ticker", ticker)
              .order("filing_date", desc=True)
              .limit(limit)
              .execute()
              .data or []
        )
        return rows
    except Exception:
        return []


def _fetch_form4_posture(db, ticker: str, lookback_days: int = 180) -> dict | None:
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        rows = (
            db.table("form4_transactions")
              .select("transaction_code, is_directional_signal, shares, price_per_share, role")
              .eq("ticker", ticker)
              .gte("transaction_date", cutoff)
              .execute()
              .data or []
        )
    except Exception:
        return None
    if not rows:
        return None
    directional = [r for r in rows if r.get("is_directional_signal")]
    buys  = sum(1 for r in directional if (r.get("transaction_code") or "").upper() == "P")
    sells = sum(1 for r in directional if (r.get("transaction_code") or "").upper() in ("S", "V"))
    return {
        "directional_buys_180d":  buys,
        "directional_sells_180d": sells,
        "total_form4_events_180d": len(rows),
    }


def _fetch_latest_transcript_verdict(db, ticker: str) -> dict | None:
    try:
        rows = (
            db.table("earnings_transcripts")
              .select("fiscal_year, fiscal_quarter, quality_grade, fetched_at")
              .eq("ticker", ticker)
              .order("fetched_at", desc=True)
              .limit(1)
              .execute()
              .data or []
        )
        return rows[0] if rows else None
    except Exception:
        return None


def build_data_section(db, candidate: dict) -> dict:
    """Closed-context DATA block for the LLM. Every key is snake_case
    and any LLM claim must reference one of these."""
    ts = candidate.get("trait_scores") or {}
    if isinstance(ts, str):
        try:
            ts = json.loads(ts)
        except Exception:
            ts = {}

    caps = candidate.get("score_caps_applied") or []
    if isinstance(caps, str):
        try:
            caps = json.loads(caps)
        except Exception:
            caps = []

    data = {
        "ticker":                       candidate.get("ticker"),
        "name":                         candidate.get("name"),
        "sector":                       candidate.get("sector"),
        "industry":                     candidate.get("industry"),
        "market_cap_usd":               candidate.get("market_cap"),
        "revenue_cagr_3y_pct":          candidate.get("revenue_cagr_3y"),
        "revenue_growth_ttm_yoy_pct":   candidate.get("revenue_growth_ttm_yoy"),
        "gross_margin_ttm_pct":         candidate.get("gross_margin_ttm"),
        "gross_margin_trend_bps":       candidate.get("gross_margin_trend"),
        "operating_margin_ttm_pct":     candidate.get("operating_margin_ttm"),
        "operating_margin_trend_bps":   candidate.get("operating_margin_trend"),
        "insider_ownership_pct":        candidate.get("insider_ownership_pct"),
        "analyst_count":                candidate.get("analyst_count"),
        "institutional_ownership_pct":  candidate.get("institutional_ownership_pct"),
        "debt_to_equity":               candidate.get("debt_to_equity"),
        "cash_runway_months":           candidate.get("cash_runway_months"),
        "multibagger_score":            candidate.get("multibagger_score"),
        "growth_quality_score":         candidate.get("growth_quality_score"),
        "reinvestment_score":           candidate.get("reinvestment_score"),
        "unit_economics_score":         candidate.get("unit_economics_score"),
        "alignment_score":              candidate.get("alignment_score"),
        "discovery_score":              candidate.get("discovery_score"),
        "trait_small_base_score":       ts.get("small_base"),
        "trait_durable_growth_score":   ts.get("durable_growth"),
        "trait_unit_econ_score":        ts.get("improving_unit_econ"),
        "trait_large_tam_score":        ts.get("large_tam_proxy"),
        "trait_alignment_score":        ts.get("aligned_owners"),
        "trait_under_discovered_score": ts.get("under_discovered"),
        "score_caps_applied":           "; ".join(c.get("reason", "") for c in caps) if caps else "none",
    }

    ticker = candidate.get("ticker")
    if ticker:
        filings = _fetch_sec_filings(db, ticker)
        for i, f in enumerate(filings, start=1):
            data[f"recent_filing_{i}_type"] = f.get("form_type")
            data[f"recent_filing_{i}_date"] = f.get("filing_date")

        form4 = _fetch_form4_posture(db, ticker)
        if form4:
            data.update(form4)

        tx = _fetch_latest_transcript_verdict(db, ticker)
        if tx:
            data["latest_earnings_year"]    = tx.get("fiscal_year")
            data["latest_earnings_quarter"] = tx.get("fiscal_quarter")
            data["latest_earnings_quality"] = tx.get("quality_grade")

    # Drop None values -- the LLM should see only what we actually know.
    return {k: v for k, v in data.items() if v not in (None, "")}


# ══════════════════════════════════════════════════════════════════════════════
# LLM
# ══════════════════════════════════════════════════════════════════════════════

def _build_prompt(data: dict) -> str:
    lines = ["DATA SECTION (every numeric or specific claim must be "
             "followed by [DATA REF: key_name]; keys below):"]
    for k, v in data.items():
        if isinstance(v, float):
            lines.append(f"  - {k} = {v:.3f}" if abs(v) < 1e4 else f"  - {k} = {v:.0f}")
        else:
            lines.append(f"  - {k} = {v}")
    lines.append("")
    lines.append("Write the thesis as STRICT JSON with the schema given. No "
                 "prose outside the JSON. Be rigorous and honest -- the "
                 "WHAT_KILLS_IT section must be as detailed and specific as "
                 "THE_10X_PATH.")
    return "\n".join(lines)


def _call_groq(prompt: str) -> tuple[str | None, str | None]:
    # Name kept for call-site stability; now routes through the shared gateway
    # (Cerebras -> Groq -> NVIDIA -> Gemini) with JSON-object output.
    # Returns (text, provider): the provider is needed so the stored row can
    # name the model that ACTUALLY answered, not the one we hoped for.
    text, provider = complete(prompt, system=SYSTEM_PROMPT, temperature=0.2,
                              max_tokens=8000, json_mode=True)
    return text, provider


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        # Some models prepend chatter; extract first {...} block
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _contains_forbidden(thesis: dict) -> list[str]:
    hits: list[str] = []
    text_blob = " ".join(str(v) for v in thesis.values() if isinstance(v, (str, list)))
    for pat in FORBIDDEN_PHRASES:
        if re.search(pat, text_blob, re.IGNORECASE):
            hits.append(pat)
    return hits


# ══════════════════════════════════════════════════════════════════════════════
# Per-candidate research
# ══════════════════════════════════════════════════════════════════════════════

THESIS_SECTIONS = [
    "selection_rationale",
    "business_model",
    "the_10x_path",
    "moat_assessment",
    "founder_assessment",
    "what_has_to_go_right",
    "what_kills_it",
]


def _has_text(s) -> bool:
    return bool(s and str(s).strip() and len(str(s).strip()) > 12)


# Honest fallbacks when a section cannot be grounded in connected sources --
# shown verbatim instead of unverified prose. NEVER fabricate; state the gap.
_LIMITED_NOTE = {
    "business_model": "Limited data — business model not verifiable from connected sources for this under-covered name.",
    "the_10x_path": "Limited data — a specific 10x path cannot be grounded in the available data yet.",
    "moat_assessment": "Limited data — moat not verifiable from connected sources.",
    "founder_assessment": "Limited data — founder / ownership detail not available from connected sources.",
    "what_has_to_go_right": "Limited data — key assumptions cannot be grounded in the available data yet.",
    "what_kills_it": "Limited data — specific failure modes not yet documented from connected sources.",
}


def research_candidate(db, candidate: dict) -> dict | None:
    ticker = candidate.get("ticker")
    if not ticker:
        return None
    log.info("Researching %s (score=%.1f)…", ticker, candidate.get("multibagger_score") or 0)

    data = build_data_section(db, candidate)
    prompt = _build_prompt(data)
    raw, provider = _call_groq(prompt)
    if not raw:
        return None
    thesis = _parse_json(raw)
    if not isinstance(thesis, dict):
        log.warning("  %s: thesis JSON unparseable", ticker)
        return None

    forbidden = _contains_forbidden(thesis)
    if forbidden:
        log.warning("  %s: forbidden phrases %s -- discarding", ticker, forbidden)
        return None

    # Section-aware verification (factcheck is deterministic -- per-section is
    # free). The selection_rationale (the theory) is the SPINE: it must verify
    # or the name is dropped -- we never show a name we cannot justify. Every
    # OTHER section that fails verification is replaced with an honest
    # limited-data note rather than shown unverified. No fabricated claim ever
    # reaches the dossier (empty-but-honest > full-but-fake).
    sr_fc = factcheck(str(thesis.get("selection_rationale") or ""), data)
    if not _has_text(thesis.get("selection_rationale")) or sr_fc["quality_grade"] == "UNVERIFIED":
        log.warning("  %s: selection rationale missing/UNVERIFIED (%.2f) -- discarding",
                    ticker, sr_fc["verification_score"])
        return None

    scores = [sr_fc["verification_score"]]
    for s in THESIS_SECTIONS:
        val = thesis.get(s)
        if s == "selection_rationale":
            thesis[s] = strip_data_refs(str(val))
            continue
        sec_fc = factcheck(str(val or ""), data)
        if not _has_text(val) or sec_fc["quality_grade"] == "UNVERIFIED":
            thesis[s] = _LIMITED_NOTE.get(
                s, "Limited data — not available from connected sources.")
        else:
            thesis[s] = strip_data_refs(str(val))
            scores.append(sec_fc["verification_score"])
    worst = min(scores)
    fc = {
        "verification_score": round(worst, 4),
        "quality_grade": "VERIFIED" if worst >= 0.95 else "PARTIALLY_VERIFIED",
        "selection_rationale_score": sr_fc["verification_score"],
    }

    return {
        "ticker": ticker,
        "multibagger_score": candidate.get("multibagger_score"),
        "conviction_tier": thesis.get("conviction_tier") or "tier_3_speculative",
        "risk_rating": thesis.get("risk_rating") or "speculative",
        "selection_rationale": thesis.get("selection_rationale"),
        "business_model": thesis.get("business_model"),
        "the_10x_path": thesis.get("the_10x_path"),
        "moat_assessment": thesis.get("moat_assessment"),
        "founder_assessment": thesis.get("founder_assessment"),
        "what_has_to_go_right": thesis.get("what_has_to_go_right"),
        "what_kills_it": thesis.get("what_kills_it"),
        "key_metrics_to_track": [
            strip_data_refs(str(m))
            for m in (thesis.get("key_metrics_to_track") or [])
            if str(m).strip()
        ],
        "verification_score": fc["verification_score"],
        "quality_grade":      fc["quality_grade"],
        "factcheck_details":  fc,
        "data_snapshot":      data,
        # The gateway fails over to whichever provider answers, so stamping
        # GROQ_MODEL unconditionally recorded a model that may never have run
        # the call -- every row written 2026-08-17..19 says
        # llama-3.3-70b-versatile, a model Groq had already retired. Record the
        # provider that actually served it.
        "llm_model":          provider or GROQ_MODEL,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def _clean_for_json(v):
    import math
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {k: _clean_for_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean_for_json(x) for x in v]
    return v


def run(top_n: int = 30, tickers: list[str] | None = None) -> int:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Restrict to the LATEST screen so we never re-research stale candidate rows
    # from prior weeks (multiple screen_dates accumulate per ticker, which was
    # producing duplicate theses per name).
    last = (db.table("multibagger_candidates").select("screen_date")
              .order("screen_date", desc=True).limit(1).execute().data or [])
    latest_screen = last[0]["screen_date"] if last else None

    q = (db.table("multibagger_candidates")
           .select("*")
           .order("multibagger_score", desc=True))
    if latest_screen:
        q = q.eq("screen_date", latest_screen)
    if tickers:
        q = q.in_("ticker", [t.upper() for t in tickers])
    else:
        q = q.eq("advanced_to_research", True)
    candidates = q.limit(top_n).execute().data or []
    if not candidates:
        log.warning("No candidates to research.")
        return 0
    log.info("Researching %d candidates", len(candidates))

    def _persist(result: dict) -> bool:
        payload = _clean_for_json({
            **{k: v for k, v in result.items()
               if k not in ("factcheck_details", "data_snapshot")},
            "factcheck_details": result.get("factcheck_details"),
            "data_snapshot": result.get("data_snapshot"),
        })
        try:
            db.table("multibagger_theses").insert(payload).execute()
            log.info("  %s: %s (grade=%s, fc=%.2f)",
                     result["ticker"], result["conviction_tier"],
                     result["quality_grade"], result["verification_score"])
            return True
        except Exception as exc:
            log.warning("  %s persist failed: %s", result.get("ticker"), exc)
            return False

    written = 0
    failed: list[dict] = []
    for cand in candidates:
        result = research_candidate(db, cand)
        if result and _persist(result):
            written += 1
        else:
            failed.append(cand)
        time.sleep(1.0)

    # One retry pass for names that failed generation or the factcheck gate
    # (transient LLM/rate-limit, or a borderline thesis that re-grounds on a
    # second pass) -- mirrors the main scan's retry discipline.
    if failed:
        log.info("retry pass: %d candidate(s) after cooldown", len(failed))
        time.sleep(20)
        for cand in failed:
            result = research_candidate(db, cand)
            if result and _persist(result):
                written += 1
            time.sleep(1.0)

    log.info("Wrote %d theses.", written)
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument("--tickers", type=str, default=None,
                    help="Comma-separated tickers (bypasses advanced_to_research filter)")
    args = ap.parse_args()
    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else None
    run(top_n=args.top_n, tickers=tickers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
