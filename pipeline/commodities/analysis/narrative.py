"""
Agent 7: narrative — the only LLM layer, CLOSED-CONTEXT.

Synthesizes a plain-English "what's driving this commodity" from the verified
outputs of agents 1-6, and nothing else. Discipline (same as the stock
engine):
  * The LLM sees ONLY a DATA section of named keys fetched/computed this run.
  * Every attempt must clear THREE gates in order: not truncated, the
    deterministic factcheck (agents.factcheck_agent.factcheck — generic
    infra, reused) at score >= 0.85 on every [DATA REF: key] claim, and a
    critic pass (second model via the llm.py gateway) auditing for
    unsupported claims, horizon leakage, and advice language. Any gate
    failure feeds its findings back into the next attempt; after
    MAX_ATTEMPTS the read is discarded and reported as absent — never
    replaced with prose from model memory.

HORIZON SEPARATION IS STRUCTURAL HERE: the tactical and structural reads are
two separate LLM calls with two separate data subsets — the tactical prompt
never sees structural framing and vice versa — so a horizon can fail
factcheck independently and a leak cannot happen by prompt construction.
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import llm  # noqa: E402 — provider-waterfall gateway (shared infra)
from agents.factcheck_agent import factcheck  # noqa: E402 — deterministic, generic
from commodities.sources.common import utcnow_iso  # noqa: E402

log = logging.getLogger("commodities.narrative")

FACTCHECK_MIN = 0.85
# 3 bounded attempts: room for one truncation + one factcheck miss before the
# read is discarded (a discarded read is reported absent, never backfilled).
MAX_ATTEMPTS = 3
# Generous headroom: [DATA REF] citations are token-heavy and the cerebras
# provider (gpt-oss-120b) spends completion tokens on reasoning first. The
# word budget in the system prompt is what keeps the text short.
MAX_TOKENS = 1400
# A narrative must end on a finished sentence (or a [DATA REF: ...] bracket).
# Without this, a max_tokens-truncated text sails through factcheck (every
# claim that survived IS cited) and gets published cut off mid-sentence.
_TERMINAL_CHARS = '.!?]"\')'

_SYSTEM = (
    "You are a commodities research writer. You write from a DATA section of "
    "named keys and NOTHING else — no outside knowledge, no remembered prices, "
    "no events not present in the data. Every specific or numeric claim MUST be "
    "immediately followed by [DATA REF: key] citing the exact key it came from. "
    "If the data does not support a statement, do not make it. This is research "
    "intelligence, NEVER advice: no buy/sell/hold language, no position sizing, "
    "no 'should'. Write 2 short paragraphs, 220 words maximum in total, plain "
    "English, measured tone. You do not need to use every DATA key — pick what "
    "matters and finish cleanly."
)

_TACTICAL_INSTRUCTIONS = (
    "Write the SHORT-TERM / TACTICAL read (about a one-week horizon, for traders): "
    "what the technicals, positioning, curve structure, latest inventory moves "
    "and seasonal tendency say right now. Do NOT discuss multi-year supply/demand "
    "or secular themes — that belongs to a different horizon and a different reader."
)

_STRUCTURAL_INSTRUCTIONS = (
    "Write the STRUCTURAL / LONG-TERM read (multi-quarter-to-multi-year horizon, "
    "for investors): what the supply/demand balance, macro drivers and longer "
    "trends say. Do NOT mention short-term technicals, day moves, RSI, or weekly "
    "positioning flows — tactical signals harm long-horizon readers."
)

_CRITIC_SYSTEM = (
    "You are a strict research auditor. Given a DATA section and a NARRATIVE, "
    "return JSON only: {\"verdict\": \"pass\"|\"fail\", \"issues\": [..]}. Fail if the "
    "narrative (1) makes any claim not supported by a DATA key, (2) uses "
    "buy/sell/hold or advice language, or (3) leaks the wrong horizon "
    "(the narrative's declared horizon is given). Minor style issues are not "
    "failures."
)


def _data_block(data: dict) -> str:
    lines = [f"{k} = {v}" for k, v in data.items() if v is not None]
    return "DATA (the ONLY permissible sources):\n" + "\n".join(lines)


def _generate(data: dict, instructions: str, horizon: str) -> dict:
    """One horizon's narrative: generate -> factcheck -> retry -> critic."""
    result: dict = {"horizon": horizon, "computed_at": utcnow_iso()}
    if not data:
        result.update({"status": "NO_DATA", "narrative": None,
                       "note": "No verified data points for this horizon — narrative skipped, not invented."})
        return result

    feedback = ""
    last_fail = None  # ("truncated"|"factcheck"|"critic", detail) of the last failed attempt
    for attempt in range(MAX_ATTEMPTS):
        prompt = (f"{_data_block(data)}\n\n{instructions}\n{feedback}")
        text, provider = llm.complete(prompt, system=_SYSTEM, temperature=0.3,
                                      max_tokens=MAX_TOKENS)
        if not text:
            result.update({"status": "LLM_UNAVAILABLE", "narrative": None})
            return result
        if text.rstrip()[-1] not in _TERMINAL_CHARS:
            last_fail = ("truncated", "cut off at the token limit")
            feedback = ("\nPREVIOUS ATTEMPT WAS CUT OFF mid-sentence (token limit). "
                        "Write the same read more concisely so it fits.")
            log.info("narrative %s truncated (attempt %d)", horizon, attempt + 1)
            continue

        fc = factcheck(text, data)
        if fc["verification_score"] < FACTCHECK_MIN:
            last_fail = ("factcheck", f"score {fc['verification_score']:.2f}")
            bad = ([u["claim"] for u in fc.get("unattributed", [])[:4]]
                   + [h["ref_key"] for h in fc.get("hallucinated_refs", [])[:4]])
            feedback = ("\nPREVIOUS ATTEMPT FAILED FACTCHECK — these claims lacked valid "
                        f"[DATA REF] support: {bad}. Every number "
                        "must cite an exact DATA key that exists in DATA.")
            log.info("narrative %s factcheck %.2f < %.2f (attempt %d)",
                     horizon, fc["verification_score"], FACTCHECK_MIN, attempt + 1)
            continue

        # Critic pass — a fail verdict feeds back into the next attempt;
        # only a clean pass (or an unavailable/unparseable critic) publishes.
        critic_prompt = (f"{_data_block(data)}\n\nNARRATIVE (declared horizon: {horizon}):\n{text}")
        verdict_text, critic_provider = llm.complete(
            critic_prompt, system=_CRITIC_SYSTEM, temperature=0.0,
            max_tokens=400, json_mode=True)
        critic: dict = {"verdict": "unavailable", "provider": critic_provider,
                        "note": "critic provider unavailable; narrative kept with deterministic factcheck only"}
        if verdict_text:
            try:
                verdict = json.loads(verdict_text)
                critic = {"verdict": verdict.get("verdict"),
                          "issues": verdict.get("issues", []),
                          "provider": critic_provider}
            except (json.JSONDecodeError, AttributeError):
                critic = {"verdict": "unparseable", "provider": critic_provider}
        if critic.get("verdict") == "fail":
            last_fail = ("critic", critic.get("issues", []))
            feedback = ("\nPREVIOUS ATTEMPT FAILED AUDIT — a research auditor flagged: "
                        f"{critic.get('issues', [])}. Fix these exactly: no claim beyond "
                        "the DATA keys, no advice language, stay in the declared horizon.")
            log.info("narrative %s critic fail (attempt %d): %s",
                     horizon, attempt + 1, critic.get("issues"))
            continue

        result.update({"status": "OK", "narrative": text, "provider": provider,
                       "factcheck": {"score": fc["verification_score"],
                                     "grade": fc["quality_grade"],
                                     "claims": fc.get("total_claims")},
                       "critic": critic})
        return result

    kind, detail = last_fail
    result.update({"status": f"DISCARDED_{kind.upper()}", "narrative": None,
                   "note": f"All {MAX_ATTEMPTS} attempts failed (last: {kind} — {detail}) "
                           "— discarded rather than published."})
    return result


def generate_reads(commodity: str, tactical_data: dict, structural_data: dict) -> dict:
    """The dual-horizon narrative pair. Inputs are FLAT dicts of citable
    key -> value built by the orchestrator from verified agent outputs only."""
    return {
        "commodity": commodity,
        "tactical": _generate(tactical_data, _TACTICAL_INSTRUCTIONS, "SHORT-TERM / TACTICAL"),
        "structural": _generate(structural_data, _STRUCTURAL_INSTRUCTIONS, "STRUCTURAL / LONG-TERM"),
        "separation_note": ("Two independent closed-context generations from two disjoint "
                            "data subsets — horizon leakage is excluded by construction "
                            "and additionally audited by the critic."),
    }
