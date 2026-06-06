"""
India source #2 -- NSE corporate announcements / filings (the SEC-EDGAR
counterpart). Surfaces datable catalysts: order wins, results, dividends,
fund-raises, management changes, concall transcripts, regulatory actions.
Free, no key, via nsepython. Symbol-filtered; each record is company-verified.
"""
from __future__ import annotations

import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")
from nsepython import nsefetch

from ._common import bare_symbol, parse_dt, verify_company

_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={}"

# Map raw announcement text/desc to a catalyst category (first match wins).
_CATS = [
    ("order_win", ["bagging", "receiving of order", "work order", "letter of award",
                   "loa", "awarded", "secures order", "bags order", "order win", "contract"]),
    ("results", ["financial result", "quarterly result", "audited", "un-audited",
                 "unaudited", "outcome of board"]),
    ("dividend", ["dividend"]),
    ("concall_transcript", ["transcript", "concall", "con. call", "earnings call",
                            "investor meet", "analyst", "institutional investor"]),
    ("fundraise", ["fund rais", "qip", "preferential", "rights issue", "ncd",
                   "debentures", "bonds", "warrants"]),
    ("rating", ["credit rating", "rating"]),
    ("mna", ["acquisition", "merger", "amalgamation", "joint venture",
             "scheme of arrangement", "stake"]),
    ("management", ["change in management", "resignation", "appointment",
                    "cessation", "senior management", "retirement"]),
    ("regulatory", ["sebi", "penalty", "show cause", "takeover regulations",
                    "order under", "insolvency", "nclt"]),
]


def classify(desc: str, text: str) -> str:
    blob = f"{desc or ''} {text or ''}".lower()
    for cat, kws in _CATS:
        if any(k in blob for k in kws):
            return cat
    return "other"


def get_announcements(symbol: str, expected_name: str | None = None,
                      days: int = 90) -> list[dict]:
    """Recent announcements for `symbol`, newest first, company-verified."""
    sym = bare_symbol(symbol)
    raw = nsefetch(_URL.format(sym))
    items = raw if isinstance(raw, list) else (raw.get("data", []) if isinstance(raw, dict) else [])
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for it in items:
        dt = parse_dt(it.get("an_dt"))
        if dt and dt < cutoff:
            continue
        rec_name = it.get("sm_name") or it.get("symbol")
        out.append({
            "date": it.get("an_dt"),
            "category": classify(it.get("desc", ""), it.get("attchmntText", "")),
            "nse_desc": it.get("desc"),
            "text": (it.get("attchmntText") or "").strip(),
            "attachment": it.get("attchmntFile"),
            "company": rec_name,
            "verified": verify_company(rec_name, expected_name),
        })
    out.sort(key=lambda r: parse_dt(r["date"]) or datetime.min, reverse=True)
    return out


def summarize(anns: list[dict]) -> dict:
    """Counts by category over the window (for the report's catalyst block)."""
    counts: dict[str, int] = {}
    for a in anns:
        counts[a["category"]] = counts.get(a["category"], 0) + 1
    return counts


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for sym, nm in [("BEL", "Bharat Electronics"), ("HBLENGINE", "HBL Engineering")]:
        a = get_announcements(sym, nm, days=60)
        print(f"\n{sym}: {len(a)} announcements (60d) | by category: {summarize(a)}")
        for x in a[:4]:
            print(f"  [{x['date']}] ({x['category']}) verified={x['verified']}: {(x['text'] or x['nse_desc'])[:80]}")
