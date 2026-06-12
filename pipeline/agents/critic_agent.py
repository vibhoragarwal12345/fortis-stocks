"""
Critic Agent  -- the devil's advocate
Every thesis the advisor reads has been challenged. For each top-ranked ticker
that already has a bull case, this agent attacks the thesis rigorously and
writes a sharper bear case -- the discipline practised at Citadel, Point72 and
Bridgewater.

It deliberately uses a DIFFERENT model from the thesis writer: debate_synthesizer
writes with Groq Llama 3.3 70B, so the critic writes with Gemini 2.0 Flash. A
model should not get to mark its own homework.

  NOTE: if GEMINI_API_KEY is not set the critic falls back to Groq Llama. That
  still produces a critique, but it forfeits the cross-model independence that
  is the whole point -- set GEMINI_API_KEY for the intended behaviour.

For each ticker it produces, and writes to ranked_focus_list (migration 012):
  critic_weaknesses, critic_disconfirming_data, critic_precedent_failures,
  critic_hidden_risks, critic_objection_level, critic_rewritten_bear_case.
The rewritten bear case also replaces the original bear_case.

CONVICTION IMPACT
  critic_objection_level is stored for the conviction grader to enforce:
    STRONG   -> conviction grade capped at B
    MODERATE -> max grade A, flagged for advisor review
    NONE     -> proceed normally
  (The cap itself is applied by conviction_grader.py.)

Usage:
    python pipeline/agents/critic_agent.py [N]      # default N = 30
"""

import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.factcheck_agent import factcheck, strip_data_refs  # noqa: E402
from config import (  # noqa: E402
    GEMINI_API_KEY, GROQ_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL,
)
from supabase import create_client  # noqa: E402
from llm import available_providers, complete  # noqa: E402 -- shared provider-waterfall gateway

DEFAULT_N    = 20              # matches run_scan.DEFAULT_TOP_N (LLM budget)
RANK_SOURCE  = "midday"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_SYSTEM = ("You are a contrarian risk officer at a hedge fund. Your job is to "
           "identify weaknesses in investment theses. You are skeptical, not "
           "negative. You ask hard questions. CLOSED CONTEXT: you may ONLY "
           "cite facts that appear in the bull case or data provided in the "
           "prompt -- never from memory. In THESIS_WEAKNESSES and "
           "REWRITTEN_BEAR_CASE, every numeric or specific claim MUST be "
           "immediately followed by a [DATA REF: key] tag (allowed keys are "
           "listed in the prompt).")

_HEADERS = ["THESIS_WEAKNESSES", "DISCONFIRMING_DATA", "PRECEDENT_FAILURES",
            "HIDDEN_RISKS", "CONVICTION_ADJUSTMENT", "REWRITTEN_BEAR_CASE"]

# ══════════════════════════════════════════════════════════════════════════════
# Prompt
# ══════════════════════════════════════════════════════════════════════════════

def _critic_data(bull_case: str, sm: dict | None) -> dict:
    """The closed-context data dict: every [DATA REF] in the critique must
    point at one of these keys (factcheck_agent verifies)."""
    data = {"bull_case": bull_case}
    if sm:
        data.update({
            "sm_pattern":           sm.get("pattern_detected"),
            "sm_confidence":        sm.get("confidence_level"),
            "sm_composite":         sm.get("composite_smart_money_score"),
            "sm_earnings_signal":   sm.get("earnings_signal_score"),
            "sm_insider_signal":    sm.get("insider_signal_score"),
            "sm_insider_cluster":   sm.get("insider_detected_cluster"),
            "sm_short_signal":      sm.get("short_signal_score"),
            "sm_short_trend":       sm.get("short_trend_direction"),
            "sm_revision_signal":   sm.get("revision_signal_score"),
            "sm_revision_velocity": sm.get("revision_velocity"),
        })
    return {k: v for k, v in data.items() if v not in (None, "")}


def _build_prompt(ticker: str, bull_case: str, sm: dict | None = None) -> str:
    sm_block = ""
    if sm:
        sm_block = (
            f"\nSMART MONEY SIGNALS for {ticker}:\n"
            f"- sm_pattern = {sm.get('pattern_detected')} "
            f"(sm_confidence = {sm.get('confidence_level')}, "
            f"sm_composite = {sm.get('composite_smart_money_score')})\n"
            f"- sm_earnings_signal = {sm.get('earnings_signal_score')}\n"
            f"- sm_insider_signal = {sm.get('insider_signal_score')} "
            f"(sm_insider_cluster = {sm.get('insider_detected_cluster')})\n"
            f"- sm_short_signal = {sm.get('short_signal_score')} "
            f"(sm_short_trend = {sm.get('short_trend_direction')})\n"
            f"- sm_revision_signal = {sm.get('revision_signal_score')} "
            f"(sm_revision_velocity = {sm.get('revision_velocity')})\n"
            "\nAddress whether these smart-money signals contradict the bull thesis "
            "in your weaknesses section.\n"
        )
    data_keys = ", ".join(_critic_data(bull_case, sm))
    return f"""Here is the bull case for {ticker} (data key: bull_case):
{bull_case}
{sm_block}
Your task: Attack this thesis rigorously.

DATA KEYS for [DATA REF] tags: {data_keys}
In THESIS_WEAKNESSES and REWRITTEN_BEAR_CASE, every numeric or specific claim
must be immediately followed by [DATA REF: key] naming where it came from
(e.g. "the claimed RSI of 62 [DATA REF: bull_case] assumes...").

THESIS_WEAKNESSES:
Identify the 3 weakest links in the bull case. Be specific -- name which claim
is weakest and why. If the smart-money signals contradict the bull thesis,
name the specific divergence.

DISCONFIRMING_DATA:
What data, if it appeared in the next 30 days, would invalidate this thesis? Be
specific about what would need to be true.

PRECEDENT_FAILURES:
Has a similar setup historically failed? If so, why?

HIDDEN_RISKS:
What risks is the bull case ignoring or downplaying? Macro, sector, idiosyncratic.

CONVICTION_ADJUSTMENT:
Based on your analysis, should conviction be raised, maintained, or lowered?
Output one of: STRONG_OBJECTION, MODERATE_OBJECTION, NO_MATERIAL_OBJECTION.

REWRITTEN_BEAR_CASE:
Now write the bear case as it should be written if it were as well-argued as
the bull case typically is. Be specific."""


# ══════════════════════════════════════════════════════════════════════════════
# LLM generation -- shared gateway (Cerebras -> Groq -> NVIDIA -> Gemini)
# ══════════════════════════════════════════════════════════════════════════════

def _generate(prompt: str) -> tuple[str | None, str]:
    return complete(prompt, system=_SYSTEM, temperature=0.5, max_tokens=1600)


# ══════════════════════════════════════════════════════════════════════════════
# Parsing
# ══════════════════════════════════════════════════════════════════════════════

def _parse(text: str) -> dict:
    pattern = re.compile(r"^[#*\s]*(" + "|".join(_HEADERS) + r")[#*:\s]*$",
                         re.MULTILINE | re.IGNORECASE)
    marks = [(m.group(1).upper(), m.end()) for m in pattern.finditer(text)]
    sections: dict[str, str] = {}
    for i, (name, end) in enumerate(marks):
        stop = (pattern.search(text, end).start()
                if i + 1 < len(marks) else len(text))
        sections[name] = text[end:stop].strip()

    adj = sections.get("CONVICTION_ADJUSTMENT", "").upper()
    if "STRONG_OBJECTION" in adj:
        level = "STRONG"
    elif "MODERATE_OBJECTION" in adj:
        level = "MODERATE"
    elif "NO_MATERIAL_OBJECTION" in adj:
        level = "NONE"
    else:                                   # unparseable -> conservative default
        level = "MODERATE"

    return {
        "critic_weaknesses":          sections.get("THESIS_WEAKNESSES") or None,
        "critic_disconfirming_data":  sections.get("DISCONFIRMING_DATA") or None,
        "critic_precedent_failures":  sections.get("PRECEDENT_FAILURES") or None,
        "critic_hidden_risks":        sections.get("HIDDEN_RISKS") or None,
        "critic_objection_level":     level,
        "critic_rewritten_bear_case": sections.get("REWRITTEN_BEAR_CASE") or None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(limit: int = DEFAULT_N, scan_id: int | None = None) -> None:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Critic Agent -- top %d, LLM waterfall: %s",
             limit, " -> ".join(available_providers()) or "NONE")

    if scan_id is not None:
        # Precise lineage (3 scans/day share run_date + run_type).
        rows = (db.table("ranked_focus_list")
                .select("ticker,rank,bull_case,bear_case,"
                        "critic_objection_level,dossier_verification_score")
                .eq("scan_id", scan_id)
                .order("rank").limit(limit).execute().data or [])
        if not rows:
            log.warning("no ranked_focus_list rows for scan_id=%s", scan_id)
            return
        run_date = None  # persist filters by scan_id below
        log.info("Critiquing scan_id=%s (%d rows)", scan_id, len(rows))
    else:
        recent = (db.table("ranked_focus_list").select("run_date")
                  .order("run_date", desc=True).limit(1).execute().data)
        if not recent:
            log.warning("ranked_focus_list empty -- run ranking_engine first.")
            return
        run_date = recent[0]["run_date"]

        rows = (db.table("ranked_focus_list")
                .select("ticker,rank,bull_case,bear_case,"
                        "critic_objection_level,dossier_verification_score")
                .eq("run_date", run_date).eq("run_type", RANK_SOURCE)
                .order("rank").limit(limit).execute().data or [])
        log.info("Critiquing %d theses from %s/%s",
                 len(rows), run_date, RANK_SOURCE)
    rows = [r for r in rows if r.get("bull_case")]
    if not rows:
        log.warning("No theses with a bull_case found -- run debate_synthesizer "
                    "first (and ensure migration 011 is applied).")
        return

    # Idempotent re-runs (mirrors debate_synthesizer): rows already carrying
    # a critique are skipped, so a partial run can be completed by simply
    # running the agent again.
    already = [r["ticker"] for r in rows if r.get("critic_objection_level")]
    rows = [r for r in rows if not r.get("critic_objection_level")]
    if already:
        log.info("%d pick(s) already critiqued (%s); processing %d",
                 len(already), ", ".join(already), len(rows))
    if not rows:
        return

    # Pre-load Smart Money signals (Step 6.7) so the critic can address them
    # and we can apply pattern-driven objection overrides post-LLM.
    tickers = [r["ticker"] for r in rows]
    smart_money: dict[str, dict] = {}
    try:
        sm_rows = (db.table("smart_money_intel")
                   .select("ticker,pattern_detected,confidence_level,"
                           "composite_smart_money_score,earnings_signal_score,"
                           "insider_signal_score,insider_detected_cluster,"
                           "short_signal_score,short_trend_direction,"
                           "revision_signal_score,revision_velocity,snapshot_date")
                   .in_("ticker", tickers)
                   .order("snapshot_date", desc=True).execute().data or [])
        for r in sm_rows:
            smart_money.setdefault(r["ticker"], r)
    except Exception as exc:
        log.debug("smart_money_intel preload failed: %s", exc)

    # Critique per ticker, with one retry pass (mirrors debate_synthesizer):
    # a transient provider failure or a factcheck rejection gets a second
    # shot instead of leaving the dossier incomplete.
    results: list[dict] = []
    failed: list[dict] = []
    queue = rows
    for attempt in (1, 2):
        if attempt == 2:
            if not failed:
                break
            queue, failed = failed, []
            log.info("retry pass: %d ticker(s) after 30s cooldown", len(queue))
            time.sleep(30)
        for r in queue:
            try:                                   # never let one ticker kill the run
                sm = smart_money.get(r["ticker"])
                text, method = _generate(_build_prompt(r["ticker"], r["bull_case"], sm))
                if not text:
                    log.warning("%s: no critique generated (attempt %d)",
                                r["ticker"], attempt)
                    failed.append(r)
                    continue
                parsed = _parse(text)
                # Same factcheck bar as the thesis writer: the fact-bearing
                # sections must tag claims with valid [DATA REF] keys.
                fc = factcheck("\n\n".join(
                    s for s in (parsed["critic_weaknesses"],
                                parsed["critic_rewritten_bear_case"]) if s),
                    _critic_data(r["bull_case"], sm))
                if fc["quality_grade"] == "UNVERIFIED":
                    log.warning("%s: critique factcheck UNVERIFIED (%.2f, "
                                "attempt %d) -- discarding", r["ticker"],
                                fc["verification_score"], attempt)
                    failed.append(r)
                    continue
                for k in ("critic_weaknesses", "critic_disconfirming_data",
                          "critic_precedent_failures", "critic_hidden_risks",
                          "critic_rewritten_bear_case"):
                    if parsed.get(k):
                        parsed[k] = strip_data_refs(parsed[k])
                parsed["ticker"] = r["ticker"]
                parsed["_method"] = method
                parsed["_fc"] = fc
                # The dossier's verification is the WEAKER of thesis and
                # critique factchecks -- both narratives reach the advisor.
                thesis_score = r.get("dossier_verification_score")
                parsed["_dossier_score"] = round(min(
                    float(thesis_score) if thesis_score is not None else 1.0,
                    fc["verification_score"]), 4)
                parsed["_original_bear"] = r.get("bear_case")
                # Pattern-driven objection override (Step 6.7). Only fires when
                # confidence is HIGH/MEDIUM (LOW = informational only per spec).
                if sm and sm.get("confidence_level") in ("HIGH", "MEDIUM"):
                    pat = sm.get("pattern_detected")
                    if pat in ("INSIDER_SMOKE_SIGNAL", "VALUE_TRAP_WARNING"):
                        if parsed["critic_objection_level"] != "STRONG":
                            log.info("%s: critic objection raised STRONG by %s pattern",
                                     r["ticker"], pat)
                            parsed["critic_objection_level"] = "STRONG"
                    elif pat == "CAPITULATION_BOTTOM":
                        # Spec: MODERATE objection if a bear case is being made.
                        # In this pipeline the critic always argues bear, so we
                        # MODERATE the objection unless it was already STRONG.
                        if parsed["critic_objection_level"] == "NONE":
                            parsed["critic_objection_level"] = "MODERATE"
                results.append(parsed)
                log.info("%s: %s objection (via %s, factcheck %s %.2f)",
                         r["ticker"], parsed["critic_objection_level"], method,
                         fc["quality_grade"], fc["verification_score"])
            except Exception as exc:
                log.warning("%s: critique iteration error -- %s",
                            r["ticker"], type(exc).__name__)
                continue

    if failed:
        log.warning("%d ticker(s) STILL missing critiques after retry: %s",
                    len(failed), ", ".join(r["ticker"] for r in failed))

    # Persist -- the rewritten bear case also replaces the original bear_case.
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    for r in results:
        payload = {
            "critic_weaknesses":          r["critic_weaknesses"],
            "critic_disconfirming_data":  r["critic_disconfirming_data"],
            "critic_precedent_failures":  r["critic_precedent_failures"],
            "critic_hidden_risks":        r["critic_hidden_risks"],
            "critic_objection_level":     r["critic_objection_level"],
            "critic_rewritten_bear_case": r["critic_rewritten_bear_case"],
            # Dossier verification = min(thesis factcheck, critique factcheck).
            "dossier_verification_score": r["_dossier_score"],
            "dossier_quality_grade":      ("VERIFIED" if r["_dossier_score"] >= 0.95
                                           else "PARTIALLY_VERIFIED"
                                           if r["_dossier_score"] >= 0.85
                                           else "UNVERIFIED"),
        }
        if r["critic_rewritten_bear_case"]:
            payload["bear_case"] = r["critic_rewritten_bear_case"]
        try:
            q = db.table("ranked_focus_list").update(payload)
            q = (q.eq("scan_id", scan_id) if scan_id is not None
                 else q.eq("run_date", run_date))
            q.eq("ticker", r["ticker"]).execute()
            updated += 1
        except Exception as exc:
            log.debug("update %s failed: %s", r["ticker"], exc)
    log.info("ranked_focus_list: critique written for %d/%d tickers",
             updated, len(results))
    if updated == 0 and results:
        log.warning("Nothing persisted -- apply supabase/migrations/"
                    "012_critic_columns.sql, then re-run.")

    _report(results)


def _report(results: list[dict]) -> None:
    bar = "=" * 80
    print(f"\n{bar}\nCRITIC AGENT -- {len(results)} theses challenged\n{bar}")
    if not results:
        return

    print("\n[ OBJECTION LEVEL BY TICKER ]")
    for r in results:
        print(f"  {r['ticker']:<8}{r['critic_objection_level']:<12}(via {r['_method']})")

    rank = {"STRONG": 3, "MODERATE": 2, "NONE": 1}
    strongest = max(results, key=lambda r: rank.get(r["critic_objection_level"], 0))
    print(f"\n[ REWRITTEN BEAR CASE -- STRONGEST OBJECTION: {strongest['ticker']} "
          f"({strongest['critic_objection_level']}) ]\n")
    print(strongest["critic_rewritten_bear_case"] or "(none)")

    print(f"\n{bar}\n[ ORIGINAL vs CRITIC-REWRITTEN BEAR CASE -- {results[0]['ticker']} ]")
    print(f"\n--- ORIGINAL (debate_synthesizer) ---\n"
          f"{results[0]['_original_bear'] or '(none on file)'}")
    print(f"\n--- CRITIC-REWRITTEN ---\n"
          f"{results[0]['critic_rewritten_bear_case'] or '(none)'}")
    print(f"{bar}\n")


if __name__ == "__main__":
    from scan_target import argv_scan_id, strip_scan_id  # noqa: E402

    _scan_id = argv_scan_id()
    _args = strip_scan_id(sys.argv[1:])
    n = DEFAULT_N
    if _args:
        try:
            n = int(_args[0])
        except ValueError:
            log.warning("Invalid N '%s' -- using %d", _args[0], DEFAULT_N)
    run(n, scan_id=_scan_id)
