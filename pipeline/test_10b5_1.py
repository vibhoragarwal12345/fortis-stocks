"""
10b5-1 spot test (Step 6.7 quality gate).

Fetches Form 4 filings for AMZN, GOOGL, META over the last 2 years, parses
every transaction, and reports our 10b5-1 detection rate broken down by
insider name. Bezos (AMZN), Pichai (GOOGL), and Zuckerberg (META) all have
publicly documented 10b5-1 plans, so the great majority of their
discretionary-looking sales should be flagged as scheduled.

Usage:
    python pipeline/test_10b5_1.py
    python pipeline/test_10b5_1.py --dump AMZN     # dump every parsed txn + footnote snippet
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agents.smart_money_intel import (  # noqa: E402
    _fetch_form4_filings, _parse_form4_xml, _resolve_cik, _looks_10b5_1,
)

TARGETS = {
    "AMZN":  ["Bezos"],
    "GOOGL": ["Pichai"],
    "META":  ["Zuckerberg"],
}

LOOKBACK_DAYS = 730


def _all_insider_txns(ticker: str) -> list[dict]:
    cik = _resolve_cik(ticker)
    if not cik:
        print(f"  {ticker}: CIK lookup failed")
        return []
    filings = _fetch_form4_filings(cik, LOOKBACK_DAYS)
    print(f"  {ticker}: {len(filings)} Form 4 filings in last {LOOKBACK_DAYS}d")
    txns = []
    import time
    for f in filings:
        try:
            txns.extend(_parse_form4_xml(cik, f["accession"]))
            time.sleep(0.10)
        except Exception:
            continue
    return txns


def _report(ticker: str, txns: list[dict], target_names: list[str], dump: bool = False):
    print(f"\n=== {ticker}  --  {len(txns)} transactions parsed ===")
    if not txns:
        print("  (no transactions)")
        return
    insiders = Counter(t["insider"] for t in txns)
    print(f"  unique insiders: {len(insiders)}")
    for name, n in insiders.most_common(8):
        n_10 = sum(1 for t in txns if t["insider"] == name and t["is_10b5_1"])
        sells = sum(1 for t in txns
                    if t["insider"] == name and (t.get("tx_code") == "S" or t.get("ad") == "D"))
        marker = "  <<<" if any(s.lower() in name.lower() for s in target_names) else ""
        print(f"    {name:<38}  n={n:<3} sells={sells:<3} 10b5-1={n_10:<3}{marker}")

    # Spot-check our target insiders specifically.
    print("\n  -- TARGET INSIDERS --")
    for name in target_names:
        matching = [t for t in txns if name.lower() in t["insider"].lower()]
        if not matching:
            print(f"    {name}: no transactions found in window")
            continue
        n = len(matching)
        n_10 = sum(1 for t in matching if t["is_10b5_1"])
        n_sells = sum(1 for t in matching
                      if t.get("tx_code") == "S" or t.get("ad") == "D")
        pct = (n_10 / n * 100) if n else 0
        verdict = "OK" if pct >= 80 else "FAIL" if pct < 50 else "WEAK"
        print(f"    {name}: n={n} sells={n_sells} 10b5-1={n_10} ({pct:.0f}%) -- {verdict}")
        # Dump 3 sample sells with footnote snippets
        sample = [t for t in matching
                  if (t.get("tx_code") == "S" or t.get("ad") == "D")][:3]
        for t in sample:
            fn = (t.get("footnote") or "")[:240].replace("\n", " ")
            print(f"      [{t.get('date')}] flag={t['is_10b5_1']}  shares={t.get('shares')}  "
                  f"value=${(t.get('value') or 0):,.0f}")
            print(f"        footnote: {fn or '(empty)'}")
    if dump:
        print("\n  -- FULL DUMP --")
        for t in txns:
            print(f"    {t.get('date')}  {t['insider']:<30}  code={t.get('tx_code')} "
                  f"ad={t.get('ad')}  shares={t.get('shares')}  10b5-1={t['is_10b5_1']}")
            if t.get("footnote"):
                print(f"      footnote: {t['footnote'][:200]}")


def main():
    dump_for = sys.argv[2] if (len(sys.argv) > 2 and sys.argv[1] == "--dump") else None
    print(f"10b5-1 SPOT TEST -- lookback={LOOKBACK_DAYS}d")
    for ticker, names in TARGETS.items():
        try:
            txns = _all_insider_txns(ticker)
            _report(ticker, txns, names, dump=(dump_for == ticker))
        except Exception as exc:
            print(f"  {ticker}: ERROR {exc}")


if __name__ == "__main__":
    main()
