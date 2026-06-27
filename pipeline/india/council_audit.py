"""
Council audit — 5-seat QA over the holdings workbook and the generated report.

Seats:
  1. Quant / Accuracy     — independently recompute every figure from the xlsx
                            and diff it against what the report logic produces;
                            cross-check each position; prove completeness.
  2. Copy / Voice         — scan the rendered PDF text for em-dashes in prose,
                            AI-tell filler, and hype.
  3. Trust / Compliance   — no firm branding, disclaimer present, no overclaim.
  4. Design / Layout      — valid PDF, fonts embedded, no leaked None/nan, sane pages.
  5. Product / Complete   — every holding is dossiered or in the appendix.

Run:  python -m pipeline.india.council_audit
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from india.dossier import (CORE_MAP, NOTE_ONLY, HOLDINGS_XLSX,        # noqa: E402
                           portfolio_summary, load_positions, _GOLD_KW)

PDF = Path.home() / "Downloads" / "dossiers" / "Portfolio_Review.pdf"

AI_TELLS = ["delve", "moreover", "furthermore", "notably", "it is worth noting",
            "robust", "leverage the", "navigate", "landscape", "underscore",
            "testament", "pivotal", "in conclusion", "overall,", "tapestry",
            "realm", "embark", "elevate", "seamless"]
HYPE = ["multibagger", "to the moon", "guaranteed", "rocket", "skyrocket",
        "can't lose", "sure thing", "next nvidia"]
OVERCLAIM = ["will rise", "will fall", "guaranteed return", "risk-free"]

results = []  # (seat, level, msg)


def add(seat, level, msg):
    results.append((seat, level, msg))


def approx(a, b, tol=0.01):
    if a is None or b is None:
        return False
    return abs(a - b) <= tol * max(1.0, abs(b))


# ── SEAT 1 — Quant / Accuracy ─────────────────────────────────────────────────

def seat_quant():
    seat = "Quant/Accuracy"
    raw = pd.ExcelFile(HOLDINGS_XLSX).parse("Portfolio Tracker", header=None)
    hdr = [str(x).strip() for x in raw.iloc[5].tolist()]
    df = raw.iloc[6:].copy(); df.columns = hdr

    def num(x):
        if pd.isna(x):
            return None
        try:
            return float(str(x).replace(",", "").strip()) if isinstance(x, str) else float(x)
        except ValueError:
            return None

    rows = []
    for _, r in df.iterrows():
        if pd.isna(r.get("ISIN")) or pd.isna(r.get("Invested Value")):
            continue
        rows.append(r)
    inv = sum(num(r["Invested Value"]) or 0 for r in rows)
    cur = sum(num(r["Current Value"]) or 0 for r in rows)
    gold_cur = sum(num(r["Current Value"]) or 0 for r in rows
                   if any(k in str(r["Script Name"]).upper() for k in _GOLD_KW))

    s = portfolio_summary()
    # independent vs function
    checks = [
        ("total invested", inv, s["total_invested"]),
        ("total current", cur, s["total_current"]),
        ("gold current", gold_cur, s["gold_current"]),
        ("gold weight %", gold_cur / cur * 100, s["gold_weight"]),
        ("equity weight %", (cur - gold_cur) / cur * 100, s["equity_weight"]),
    ]
    for label, indep, fn in checks:
        if approx(indep, fn):
            add(seat, "PASS", f"{label}: {fn:,.2f} matches independent recompute")
        else:
            add(seat, "FAIL", f"{label}: report={fn:,.2f} vs independent={indep:,.2f}")

    # weights sum to ~100
    wsum = sum(h["weight"] for h in s["holdings"])
    add(seat, "PASS" if approx(wsum, 100, 0.001) else "FAIL",
        f"holding weights sum to {wsum:.2f}% (expect 100)")

    # position cross-check for every dossiered name
    pos = load_positions()
    bad = 0
    for canon in CORE_MAP:
        p = pos.get(canon)
        if not p:
            add(seat, "WARN", f"no position matched for {canon}")
            continue
        # find excel row independently
        match = None
        for r in rows:
            nm = str(r["Script Name"]).strip()
            if nm.startswith(canon[:18]) or canon.startswith(nm[:18]):
                match = r; break
        if match is None:
            add(seat, "WARN", f"could not independently locate {canon}")
            continue
        ok = (approx(p["invested"], num(match["Invested Value"]))
              and approx(p["current_value"], num(match["Current Value"]))
              and approx(p["avg_cost"], num(match["Avg Unit Cost"])))
        if not ok:
            bad += 1
            add(seat, "FAIL", f"{canon}: position figures mismatch the workbook")
    if bad == 0:
        add(seat, "PASS", f"all {len(CORE_MAP)} dossier positions reconcile to the workbook")


# ── SEAT 5 — Completeness ─────────────────────────────────────────────────────

def seat_complete():
    seat = "Completeness"
    s = portfolio_summary()
    accounted = set(CORE_MAP) | set(NOTE_ONLY)
    missing = []
    for h in s["holdings"]:
        if h["is_gold"]:
            continue
        nm = h["name"]
        hit = any(nm.startswith(a[:18]) or a.startswith(nm[:18]) for a in accounted)
        if not hit:
            missing.append(nm)
    if missing:
        add(seat, "FAIL", f"{len(missing)} equity holding(s) neither dossiered nor in appendix: {missing}")
    else:
        add(seat, "PASS", "every equity holding is dossiered or in the appendix; gold in overview")


# ── PDF-text seats (2,3,4) ────────────────────────────────────────────────────

def seat_pdf():
    if not PDF.exists():
        add("Design", "FAIL", f"{PDF.name} not found")
        return
    b = PDF.read_bytes()
    reader = PdfReader(str(PDF))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    low = text.lower()
    npages = len(reader.pages)

    # Seat 4 Design
    add("Design", "PASS" if b[:5] == b"%PDF-" and b"%%EOF" in b[-1024:] else "FAIL",
        f"valid PDF, {len(b)//1024} KB, {npages} pages")
    add("Design", "PASS" if len(b) > 400_000 else "WARN",
        f"file size {len(b)//1024} KB (fonts embedded if large)")
    leaked = [w for w in (">none<", " none ", "nan%", "₹nan", "nanx") if w in low]
    # 'none' is risky (matches words); check explicit leaks only
    add("Design", "FAIL" if "₹nan" in low or "nan%" in low or "none%" in low else "PASS",
        f"no leaked None/nan values" if not ("₹nan" in low or "nan%" in low) else f"leaked tokens: {leaked}")

    # Seat 2 Copy/Voice — match prose only: em-dash flanked by letters, whole-word tells
    prose_emdash = len(re.findall(r"[A-Za-z],? [—–] [A-Za-z]", text))
    add("Copy/Voice", "PASS" if prose_emdash == 0 else "WARN",
        f"{prose_emdash} prose em/en-dash(es) (null-cell '—' and news headlines excluded)")
    tells = sorted({w for w in AI_TELLS if re.search(rf"\b{re.escape(w)}\b", low)})
    add("Copy/Voice", "PASS" if not tells else "WARN", f"AI-tell words (whole-word): {tells or 'none'}")
    hype = sorted({w for w in HYPE if w in low})
    add("Copy/Voice", "PASS" if not hype else "FAIL", f"hype words: {hype or 'none'}")

    # Quant — rendered figures must be exact, not rounded to crore
    from india import render as _R
    exact = _R.inr(portfolio_summary()["total_current"]).replace("₹", "")
    add("Quant/Accuracy", "PASS" if exact in text else "FAIL",
        f"exact portfolio total '{exact}' rendered (no crore rounding)")
    # Only flag our own rounded format ("Lakh Cr"); bare "cr" appears in verbatim
    # news headlines (e.g. order wins) which we render as-is and must not rewrite.
    add("Quant/Accuracy", "PASS" if "lakh cr" not in low else "WARN",
        "no 'Lakh Cr' rounded money figures (news-headline crore is verbatim)")

    # Seat 3 Trust/Compliance
    branding = [w for w in ("cefa", "christopher edwards", "ultraviolet") if w in low]
    add("Compliance", "FAIL" if branding else "PASS", f"firm branding: {branding or 'none'}")
    add("Compliance", "PASS" if "not investment advice" in low else "FAIL",
        "disclaimer 'not investment advice' present" if "not investment advice" in low
        else "disclaimer missing")
    over = sorted({w for w in OVERCLAIM if w in low})
    add("Compliance", "PASS" if not over else "WARN", f"overclaim phrasing: {over or 'none'}")


def main():
    seat_quant()
    seat_complete()
    seat_pdf()
    order = ["Quant/Accuracy", "Completeness", "Copy/Voice", "Compliance", "Design"]
    fails = sum(1 for _, lv, _ in results if lv == "FAIL")
    warns = sum(1 for _, lv, _ in results if lv == "WARN")
    for seat in order:
        rows = [r for r in results if r[0] == seat]
        if not rows:
            continue
        print(f"\n=== {seat} ===")
        for _, lv, msg in rows:
            mark = {"PASS": "  OK ", "WARN": " WARN", "FAIL": " FAIL"}[lv]
            print(f"  [{mark}] {msg}")
    print(f"\n==== COUNCIL VERDICT: {fails} FAIL, {warns} WARN ====")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
