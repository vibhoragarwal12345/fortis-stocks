"""
Form 4 transaction-code + 10b5-1 spot test (data-integrity quality gate).

Fetches Form 4 filings for AMZN, GOOGL, META, parses every transaction, and
reports two things:

  1. The transaction-code breakdown and how many transactions are DIRECTIONAL
     signals (code P, code S not 10b5-1, code V) versus mechanical events
     that must NOT count as insider sentiment -- grants (A), tax withholding
     (F), gifts (G), derivative exercises (M). Bezos's charitable gifts and
     Pichai's GSU vestings should appear in the code histogram but be
     EXCLUDED from the directional count.

  2. The 10b5-1 detection rate. Bezos, Pichai and Zuckerberg all have
     publicly documented 10b5-1 plans, so most of their code-S sales should
     be flagged as scheduled (and therefore non-directional).

Usage:
    python pipeline/test_10b5_1.py
    python pipeline/test_10b5_1.py --days 90       # shorter lookback window
    python pipeline/test_10b5_1.py --dump AMZN     # dump every parsed txn
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


def _all_insider_txns(ticker: str, lookback_days: int = LOOKBACK_DAYS) -> list[dict]:
    cik = _resolve_cik(ticker)
    if not cik:
        print(f"  {ticker}: CIK lookup failed")
        return []
    filings = _fetch_form4_filings(cik, lookback_days)
    print(f"  {ticker}: {len(filings)} Form 4 filings in last {lookback_days}d")
    txns = []
    import time
    for f in filings:
        try:
            txns.extend(_parse_form4_xml(cik, f["accession"]))
            time.sleep(0.10)
        except Exception:
            continue
    return txns


def _report(ticker: str, txns: list[dict], target_names: list[str],
            dump: bool = False):
    print(f"\n=== {ticker}  --  {len(txns)} transactions parsed ===")
    if not txns:
        print("  (no transactions)")
        return

    # Transaction-code histogram. Gifts (G), grants (A) and derivative
    # exercises (M) should be present but NOT counted as directional signals.
    codes = Counter(t.get("tx_code") or "?" for t in txns)
    directional = sum(1 for t in txns if t.get("is_directional_signal"))
    print("  transaction codes: "
          + ", ".join(f"{c}={n}" for c, n in sorted(codes.items())))
    print(f"  directional signals (P / S non-10b5-1 / V): {directional} of "
          f"{len(txns)}  ({len(txns) - directional} excluded as mechanical)")

    insiders = Counter(t["insider"] for t in txns)
    print(f"  unique insiders: {len(insiders)}")
    for name, n in insiders.most_common(8):
        n_10 = sum(1 for t in txns if t["insider"] == name and t["is_10b5_1"])
        n_dir = sum(1 for t in txns
                    if t["insider"] == name and t.get("is_directional_signal"))
        marker = "  <<<" if any(s.lower() in name.lower()
                                for s in target_names) else ""
        print(f"    {name:<38}  n={n:<3} directional={n_dir:<3} "
              f"10b5-1={n_10:<3}{marker}")

    # Spot-check the target insiders specifically.
    print("\n  -- TARGET INSIDERS --")
    for name in target_names:
        matching = [t for t in txns if name.lower() in t["insider"].lower()]
        if not matching:
            print(f"    {name}: no transactions found in window")
            continue
        n = len(matching)
        n_10 = sum(1 for t in matching if t["is_10b5_1"])
        n_dir = sum(1 for t in matching if t.get("is_directional_signal"))
        code_bd = dict(sorted(Counter(t.get("tx_code") or "?"
                                      for t in matching).items()))
        n_sells_s = sum(1 for t in matching if t.get("tx_code") == "S")
        # 10b5-1 share is computed on code-S sales only (filing-level flags
        # also tag non-S transactions, which would otherwise inflate the rate
        # past 100%).
        n_10_s = sum(1 for t in matching
                     if t.get("tx_code") == "S" and t["is_10b5_1"])
        print(f"    {name}: n={n}  codes={code_bd}")
        if n_sells_s == 0:
            print(f"      directional={n_dir}  code-S sales=0  -- "
                  f"10b5-1 detection n/a (no open-market sales)")
        else:
            pct_10 = n_10_s / n_sells_s * 100
            # Informational only -- a low rate may mean the insider genuinely
            # isn't on a 10b5-1 plan (e.g. trust dispositions), which is the
            # correct detection outcome, not a code bug.
            verdict = ("OK" if pct_10 >= 80
                       else "low (may be non-10b5-1 by design)" if pct_10 < 50
                       else "weak")
            print(f"      directional={n_dir}  code-S sales={n_sells_s}  "
                  f"10b5-1 sales={n_10_s}/{n_sells_s} ({pct_10:.0f}%) -- "
                  f"10b5-1 detection {verdict}")
        excluded = [t for t in matching if not t.get("is_directional_signal")]
        if excluded:
            ex_codes = dict(sorted(Counter(t.get("tx_code") or "?"
                                           for t in excluded).items()))
            print(f"      EXCLUDED from insider signal ({len(excluded)} txns): "
                  f"{ex_codes} -- gifts/grants/vestings/10b5-1, correctly filtered")

    if dump:
        print("\n  -- FULL DUMP --")
        for t in txns:
            print(f"    {t.get('date')}  {t['insider']:<30}  "
                  f"code={t.get('tx_code')} ad={t.get('ad')}  "
                  f"shares={t.get('shares')}  10b5-1={t['is_10b5_1']}  "
                  f"directional={t.get('is_directional_signal')}")
            if t.get("footnote"):
                print(f"      footnote: {t['footnote'][:200]}")


def main():
    args = sys.argv[1:]
    lookback = LOOKBACK_DAYS
    if "--days" in args:
        i = args.index("--days")
        if i + 1 < len(args):
            lookback = int(args[i + 1])
    dump_for = None
    if "--dump" in args:
        i = args.index("--dump")
        if i + 1 < len(args):
            dump_for = args[i + 1]
    print(f"FORM 4 TRANSACTION-CODE + 10b5-1 SPOT TEST -- lookback={lookback}d")
    for ticker, names in TARGETS.items():
        try:
            txns = _all_insider_txns(ticker, lookback)
            _report(ticker, txns, names, dump=(dump_for == ticker))
        except Exception as exc:
            print(f"  {ticker}: ERROR {exc}")


if __name__ == "__main__":
    main()
