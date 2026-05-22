"""
Hallucination Check
Scans every persisted thesis on ranked_focus_list for numerical claims that
the LLM likely fabricated from its training data rather than sourcing from
the pipeline. Flags include:

  old_year_specific     "Q3 2023 earnings" in a 2026 report -- specific dates
                        outside the current cycle are almost always fabricated.
  specific_pe_ratio     "P/E of 64.1" -- multi-decimal P/E ratios rarely come
                        from our pipeline data; usually LLM-supplied.
  specific_debt_amount  "$9.5B in debt" -- exact debt figures we don't pass in.
  specific_margin       "gross margin of 41.4%" -- specific margins we
                        don't pass in.
  founded_year          "founded in 1976" -- training-data lore.
  fda_drug_specific     "Phase 3 trial for compound XX-123" -- biotech detail
                        we don't have.

This is a heuristic scan, not a strict fact-check (we didn't build the full
factcheck_agent from Part 2). High concern counts identify theses that need
re-reading by an advisor before any client-facing use.

Usage:
    python pipeline/hallucination_check.py
"""

import logging
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

CURRENT_YEAR = datetime.now(timezone.utc).year

# Per-pattern regex + a short description of why it's suspicious.
PATTERNS = [
    ("old_year_specific",
     re.compile(r"\b(?:Q[1-4]\s+)?(20[12]\d)\b"),
     "specific year tag"),
    ("specific_pe_ratio",
     re.compile(r"\bP/E(?:\s+ratio)?\s+(?:of\s+)?(\d+\.\d{1,2})\b", re.IGNORECASE),
     "P/E with decimal precision"),
    ("specific_debt_amount",
     re.compile(r"\$\d+(?:\.\d+)?\s*[BM]\s+(?:in\s+|of\s+)?(?:debt|long-term debt)",
                re.IGNORECASE),
     "exact debt figure"),
    ("specific_margin",
     re.compile(r"\b(?:gross|operating|net|EBITDA)\s+margin\s+of\s+\d+(?:\.\d+)?%",
                re.IGNORECASE),
     "specific margin %"),
    ("founded_year",
     re.compile(r"\bfounded\s+in\s+(\d{4})\b", re.IGNORECASE),
     "company history claim"),
    ("specific_revenue_amount",
     re.compile(r"\$\d+(?:\.\d+)?\s*[BM]\s+in\s+revenue", re.IGNORECASE),
     "specific revenue $"),
    ("phase_trial",
     re.compile(r"\bPhase\s+[123I]+\b", re.IGNORECASE),
     "biotech trial phase"),
]


def _scan(text: str) -> list[tuple[str, str, str]]:
    """Return list of (label, match, why) for suspicious claims in `text`."""
    if not text:
        return []
    findings = []
    for label, pat, why in PATTERNS:
        for m in pat.finditer(text):
            full = m.group(0)
            # Old-year filter: only flag years older than the current cycle.
            if label == "old_year_specific":
                try:
                    yr = int(m.group(1))
                    if yr >= CURRENT_YEAR - 1:
                        continue
                except (ValueError, IndexError):
                    continue
            findings.append((label, full, why))
    return findings


def run() -> None:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    rows = (db.table("ranked_focus_list")
            .select("ticker,bull_case,bear_case,critic_rewritten_bear_case,"
                    "thesis_generated_at,run_type")
            .eq("run_type", "midday").not_.is_("bull_case", "null")
            .execute().data or [])

    bar = "=" * 78
    print(f"\n{bar}\nHALLUCINATION CHECK  --  {len(rows)} theses scanned  "
          f"(current year {CURRENT_YEAR})\n{bar}")

    by_type = Counter()
    per_ticker = {}
    for r in rows:
        all_findings = []
        for field in ("bull_case", "bear_case", "critic_rewritten_bear_case"):
            for f in _scan(r.get(field)):
                all_findings.append((field,) + f)
                by_type[f[0]] += 1
        if all_findings:
            per_ticker[r["ticker"]] = all_findings

    print(f"\n[ SUSPICIOUS CLAIM TYPES -- raw counts across all theses ]")
    if by_type:
        for label, n in by_type.most_common():
            why = next((w for L, _, w in PATTERNS if L == label), "")
            print(f"  {label:<26}{n:>4}    {why}")
    else:
        print("  (no suspicious patterns matched)")

    if per_ticker:
        print(f"\n[ TICKERS WITH SUSPICIOUS CLAIMS -- {len(per_ticker)} / "
              f"{len(rows)} theses ]")
        ranked = sorted(per_ticker.items(), key=lambda x: -len(x[1]))
        for t, findings in ranked[:25]:
            print(f"\n  {t}  ({len(findings)} claim{'s' if len(findings)!=1 else ''})")
            for field, label, match, _ in findings[:6]:
                print(f"    [{field[:11]:<11} {label:<22}]  \"{match}\"")
            if len(findings) > 6:
                print(f"    ... and {len(findings)-6} more")

    print(f"\n{bar}")
    if per_ticker:
        clean = len(rows) - len(per_ticker)
        print(f"  Clean (no flagged claims):  {clean}/{len(rows)}")
        print(f"  Theses with flagged claims: {len(per_ticker)}/{len(rows)}")
        print(f"  Total flagged claims:       {sum(by_type.values())}")
    else:
        print(f"  All {len(rows)} theses clean -- no fabricated-specific claims detected.")
    print(bar + "\n")


if __name__ == "__main__":
    run()
