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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

GROQ_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are a small-cap growth analyst who has studied the
traits of historical 100-baggers (small base, long runways, high returns on
capital, owner-operators). You write rigorous, BALANCED multibagger theses.

CRITICAL RULES
- You may ONLY cite facts from the provided DATA section. Every numeric
  or specific claim MUST be immediately followed by [DATA REF: key_name],
  where key_name is one of the snake_case keys in the DATA section.
- You are honest about risk -- most multibagger candidates fail. The
  WHAT_KILLS_IT section MUST be as rigorous and specific as THE_10X_PATH.
- Never use hype language. Forbidden phrases include: "to the moon",
  "can't miss", "guaranteed", "next Nvidia", "no-brainer", "moonshot",
  "10-bagger lock", "easy 10x". Reject these in your own writing.
- Use plain English. No marketing speak.
- If the data is thin in a section, say so honestly ("DATA section does
  not provide enough information to assess X"). Do not fabricate context.

OUTPUT FORMAT
Return STRICT JSON with these exact keys. No prose outside the JSON.

{
  "business_model": "<plain English: what they do, how they earn revenue>",
  "the_10x_path":   "<concrete path: what TAM capture, margin expansion, multiple re-rating; cite metrics>",
  "moat_assessment":"<honest: network effects / switching costs / brand / IP / NONE>",
  "founder_assessment":"<who runs it, alignment, track record from data only>",
  "what_has_to_go_right":"<3-4 key assumptions the thesis depends on>",
  "what_kills_it": "<3-4 specific failure modes -- as rigorous as the 10x path>",
  "key_metrics_to_track":["metric1","metric2","metric3","metric4"],
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
              .select("form_type, filing_date, filing_url, title")
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
              .select("transaction_code, is_directional_signal, transaction_shares, transaction_price, person_title")
              .eq("ticker", ticker)
              .gte("filing_date", cutoff)
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
              .select("year, quarter, quality_tier, sentiment_score, fetched_at")
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
            data["latest_earnings_year"]    = tx.get("year")
            data["latest_earnings_quarter"] = tx.get("quarter")
            data["latest_earnings_quality"] = tx.get("quality_tier")
            data["latest_earnings_sentiment"] = tx.get("sentiment_score")

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


def _call_groq(prompt: str) -> str | None:
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY missing -- cannot generate thesis.")
        return None
    try:
        from groq import Groq
        resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        log.warning("Groq call failed: %s", exc)
        return None


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
    "business_model",
    "the_10x_path",
    "moat_assessment",
    "founder_assessment",
    "what_has_to_go_right",
    "what_kills_it",
]


def research_candidate(db, candidate: dict) -> dict | None:
    ticker = candidate.get("ticker")
    if not ticker:
        return None
    log.info("Researching %s (score=%.1f)…", ticker, candidate.get("multibagger_score") or 0)

    data = build_data_section(db, candidate)
    prompt = _build_prompt(data)
    raw = _call_groq(prompt)
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

    # Run factcheck across each prose section.
    combined = "\n\n".join(str(thesis.get(s, "")) for s in THESIS_SECTIONS)
    fc = factcheck(combined, data)

    return {
        "ticker": ticker,
        "multibagger_score": candidate.get("multibagger_score"),
        "conviction_tier": thesis.get("conviction_tier") or "tier_3_speculative",
        "risk_rating": thesis.get("risk_rating") or "speculative",
        "business_model": thesis.get("business_model"),
        "the_10x_path": thesis.get("the_10x_path"),
        "moat_assessment": thesis.get("moat_assessment"),
        "founder_assessment": thesis.get("founder_assessment"),
        "what_has_to_go_right": thesis.get("what_has_to_go_right"),
        "what_kills_it": thesis.get("what_kills_it"),
        "key_metrics_to_track": thesis.get("key_metrics_to_track") or [],
        "verification_score": fc["verification_score"],
        "quality_grade":      fc["quality_grade"],
        "factcheck_details":  fc,
        "data_snapshot":      data,
        "llm_model":          GROQ_MODEL,
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

    q = (db.table("multibagger_candidates")
           .select("*")
           .order("multibagger_score", desc=True))
    if tickers:
        q = q.in_("ticker", [t.upper() for t in tickers])
    else:
        q = q.eq("advanced_to_research", True)
    candidates = q.limit(top_n).execute().data or []
    if not candidates:
        log.warning("No candidates to research.")
        return 0
    log.info("Researching %d candidates", len(candidates))

    written = 0
    for cand in candidates:
        result = research_candidate(db, cand)
        if not result:
            continue
        payload = _clean_for_json({
            **{k: v for k, v in result.items()
               if k not in ("factcheck_details", "data_snapshot")},
            "factcheck_details": result.get("factcheck_details"),
            "data_snapshot": result.get("data_snapshot"),
        })
        try:
            db.table("multibagger_theses").insert(payload).execute()
            written += 1
            log.info("  %s: %s (grade=%s, fc=%.2f)",
                     result["ticker"], result["conviction_tier"],
                     result["quality_grade"], result["verification_score"])
        except Exception as exc:
            log.warning("  %s persist failed: %s", result.get("ticker"), exc)
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
