"""
Factcheck Agent
Minimal, deterministic verification of LLM-generated narratives that follow
the "every specific claim must be followed by [DATA REF: source_key]" rule.

Design contract for the upstream prompt:
  - The LLM is given a DATA section with named keys (e.g. "operating_margin",
    "peer_median_pe", "industry_stance").
  - Every numeric / specific claim in the output MUST be immediately followed
    by a token of the form [DATA REF: key_name].
  - The factcheck pass:
      1. extracts every numeric / specific claim,
      2. confirms each claim has a [DATA REF] tag within the next ~120 chars,
      3. confirms the referenced key exists in the input data dictionary,
      4. when the referenced value is numeric, confirms the claim's number
         is within tolerance of the input.
  - Tags that point to keys not in the data dictionary are HALLUCINATED REFS.
  - Numeric claims without ANY adjacent [DATA REF] are UNATTRIBUTED.

Scoring:
  verification_score = verified_claims / total_claims  (or 1.0 if no claims)
  quality_grade:
    VERIFIED            score >= 0.95
    PARTIALLY_VERIFIED  score >= 0.85
    UNVERIFIED          score < 0.85

Usage (module):
    from agents.factcheck_agent import factcheck
    result = factcheck(text, data_dict)
"""

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

# A "specific claim" is a number, percentage, ratio, currency amount, or a
# quoted ticker. We deliberately do NOT match bare years (too noisy) or 1-2
# digit ranks that the narrative may mention without a citation.
NUM_PATTERN = re.compile(
    r"""(?P<full>
            (?:[-+]?\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?[BMK]?%?)  # 1,234 or 1,234.5B
          | (?:[-+]?\$?\d+\.\d+[BMK]?%?)                       # 12.5%, 1.23B
          | (?:[-+]?\$\d+[BMK]?)                               # $12B
          | (?:[-+]?\d+%)                                      # 12%
        )
    """,
    re.VERBOSE,
)

REF_PATTERN = re.compile(r"\[DATA\s*REF:\s*([a-zA-Z0-9_.\-\s]+?)\]", re.IGNORECASE)
TICKER_QUOTE_PATTERN = re.compile(r"\b[A-Z]{2,5}\b")

REF_LOOKAHEAD_CHARS = 140       # how far after a claim to look for the [DATA REF] tag
NUMERIC_TOLERANCE   = 0.02      # +/- 2% on absolute value when the data value is itself numeric


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _to_float(s: str) -> float | None:
    """Parse '12.5%', '$1.2B', '1,234' -> float (raw magnitude, ignoring units)."""
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("$", "").replace("%", "")
    mult = 1.0
    if s.endswith("B"):
        mult, s = 1e9, s[:-1]
    elif s.endswith("M"):
        mult, s = 1e6, s[:-1]
    elif s.endswith("K"):
        mult, s = 1e3, s[:-1]
    try:
        return float(s) * mult
    except (ValueError, TypeError):
        return None


def _refs_after(text: str, end: int) -> list[tuple[str, int, int]]:
    """[(ref_key_normalized, span_start, span_end)] for any [DATA REF] tags in
    text[end : end + REF_LOOKAHEAD_CHARS]."""
    window = text[end:end + REF_LOOKAHEAD_CHARS]
    out = []
    for m in REF_PATTERN.finditer(window):
        out.append((m.group(1).strip().lower(),
                    end + m.start(),
                    end + m.end()))
    return out


def _flatten_data(data: dict[str, Any]) -> dict[str, str]:
    """Return {key_lower: str_value} -- supports one level of nesting so the
    LLM can use either 'operating_margin' or 'peer_median.pe' style refs."""
    out = {}
    for k, v in data.items():
        kl = str(k).lower()
        if isinstance(v, dict):
            for sk, sv in v.items():
                out[f"{kl}.{str(sk).lower()}"] = str(sv)
            out[kl] = str(v)
        else:
            out[kl] = str(v)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def factcheck(text: str, data: dict[str, Any]) -> dict:
    """Verify every numeric claim in `text` is followed by a [DATA REF: key]
    tag whose key exists in `data`. Returns a result dict.

    The result dict contains:
        verification_score (float, 0..1)
        quality_grade      (str)
        total_claims       (int)
        verified_claims    (int)
        unattributed       (list of {claim, position})
        hallucinated_refs  (list of {ref_key, position})
        value_mismatches   (list of {claim, ref_key, expected, found})
    """
    if not text:
        return {"verification_score": 1.0, "quality_grade": "VERIFIED",
                "total_claims": 0, "verified_claims": 0,
                "unattributed": [], "hallucinated_refs": [],
                "value_mismatches": []}

    flat = _flatten_data(data or {})
    text_l = text

    unattributed: list[dict] = []
    hallucinated: list[dict] = []
    mismatches:   list[dict] = []
    total = 0
    verified = 0

    for m in NUM_PATTERN.finditer(text_l):
        claim = m.group("full")
        # Filter trivial matches: bare 1- or 2-digit integers ("3 peers", "12
        # months", etc.) -- those aren't the kind of numeric specificity the
        # spec is trying to police.
        bare_int = re.fullmatch(r"\d{1,2}", claim)
        if bare_int:
            continue
        total += 1
        refs = _refs_after(text_l, m.end())
        if not refs:
            unattributed.append({"claim": claim, "position": m.start()})
            continue

        # Take the first ref that points to a valid key. If none do, all are
        # hallucinated (record them) but the claim is unverified.
        ok = False
        for key, _, _ in refs:
            if key in flat:
                ok = True
                # Optional numeric consistency check.
                claim_val = _to_float(claim)
                data_val  = _to_float(flat[key])
                if claim_val is not None and data_val is not None:
                    tol = abs(data_val) * NUMERIC_TOLERANCE + 1e-9
                    if abs(claim_val - data_val) > tol:
                        # The number doesn't match the cited data point.
                        mismatches.append({
                            "claim": claim, "ref_key": key,
                            "expected": flat[key], "found": claim,
                        })
                        ok = False
                        break
                break
            else:
                hallucinated.append({"ref_key": key, "position": m.end()})
        if ok:
            verified += 1
        elif not refs:
            unattributed.append({"claim": claim, "position": m.start()})

    score = (verified / total) if total else 1.0
    if score >= 0.95:
        grade = "VERIFIED"
    elif score >= 0.85:
        grade = "PARTIALLY_VERIFIED"
    else:
        grade = "UNVERIFIED"

    return {
        "verification_score": round(score, 4),
        "quality_grade":      grade,
        "total_claims":       total,
        "verified_claims":    verified,
        "unattributed":       unattributed,
        "hallucinated_refs":  hallucinated,
        "value_mismatches":   mismatches,
    }


# Display stripping. Handles well-formed AND multi-key tags ("[DATA REF: a, b]")
# but the character class EXCLUDES '[' as well as ']' -- so a malformed/unclosed
# tag can NEVER consume real prose up to a LATER tag's bracket. (The old
# `[^\]]*` did exactly that: an unclosed "[DATA REF: ..." swallowed everything
# to the next tag's ']', truncating the dossier mid-sentence -- June 2026 bug.)
_STRIP_PATTERN = re.compile(r"\[DATA\s*REF:[^\[\]]*\]", re.IGNORECASE)

# A complete prose section is substantial AND ends on a sentence boundary. The
# displayed dossier must NEVER end mid-word/mid-sentence -- that reads as broken
# or fabricated to a client.
_TERMINAL_PUNCT = (".", "!", "?", '"', "'", ")", "”", "’", "…")


def strip_data_refs(text: str) -> str:
    """Remove [DATA REF: x] tags for clean display in reports.

    Only well-formed (closed) tags are stripped. An UNCLOSED remnant is left in
    place ON PURPOSE -- trying to strip it cannot tell the malformed key from
    the prose after it, so it would risk eating real text. Instead looks_complete
    rejects any section that still contains a remnant, so it gets regenerated
    (debate retry) or hidden (gate) -- never displayed. Prose is never lost.
    """
    if not text:
        return ""
    out = _STRIP_PATTERN.sub("", text)
    out = re.sub(r"[ \t]+([,.;:!?])", r"\1", out)   # no orphan space before punctuation
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def looks_complete(text, min_len: int = 400) -> bool:
    """True if `text` reads as a finished prose section: long enough to be real,
    ending on a sentence boundary, and carrying no leftover [DATA REF tag. Catches
    LLM stubs, mid-sentence truncation, and malformed-tag remnants so a half-
    written or messy bull/bear case never reaches a client."""
    if not text:
        return False
    t = str(text).rstrip()
    if "[data ref" in t.lower():                    # leftover malformed tag
        return False
    return len(t) >= min_len and t.endswith(_TERMINAL_PUNCT)


# ══════════════════════════════════════════════════════════════════════════════
# CLI demo
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    demo_text = ("Target trades at 25.5x P/E [DATA REF: target_pe] vs peer "
                 "median 31.2x [DATA REF: peer_median_pe], a 22% discount "
                 "[DATA REF: pe_discount_pct]. Operating margin 28.4% "
                 "[DATA REF: target_op_margin] beats the peer median 18.0% "
                 "[DATA REF: peer_median_op_margin] by 1040 bps "
                 "[DATA REF: op_margin_delta_bps]. ROE 41% with no source.")
    demo_data = {
        "target_pe":              "25.5",
        "peer_median_pe":         "31.2",
        "pe_discount_pct":        "22%",
        "target_op_margin":       "28.4%",
        "peer_median_op_margin":  "18.0%",
        "op_margin_delta_bps":    "1040",
    }
    r = factcheck(demo_text, demo_data)
    print(r)
