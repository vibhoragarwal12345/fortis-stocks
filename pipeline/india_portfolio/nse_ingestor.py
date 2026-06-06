"""
NSE ingestor (India data layer #2/#3/#4).
=========================================
Free, no-API-key NSE sources via nsepython, the Indian counterparts to our
US pipeline:

  get_announcements()  -> corporate filings/announcements   (~SEC EDGAR)
  get_insider_trades() -> SEBI SAST/PIT disclosures          (~Form 4)
  get_events()         -> board-meeting / results calendar   (~earnings cal)
  get_fno_ban_list()   -> daily F&O securities-in-ban        (~crowding/short)
  is_in_fno_ban()      -> membership check for one symbol

Notes
- NSE blocks datacenter IPs; these calls are reliable from a residential IP.
- Symbols are the bare NSE trading symbol (BEL, HAL), not the yfinance ".NS".
- Every function returns plain dicts/lists so callers can tag provenance as
  [VERIFIED: NSE live] in reports.
"""
from __future__ import annotations

import csv
import io
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")
import requests
from nsepython import nsefetch

_BAN_CSV = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
_HDR = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}


def bare_symbol(ticker: str) -> str:
    """'BEL.NS' / 'BEL.BO' -> 'BEL'."""
    return ticker.split(".")[0].upper()


def _parse_dt(s: str):
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%d-%b-%Y %H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ── Announcements (~SEC EDGAR) ────────────────────────────────────────────────
def get_announcements(symbol: str, days: int = 90) -> list[dict]:
    sym = bare_symbol(symbol)
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={sym}"
    raw = nsefetch(url)
    items = raw if isinstance(raw, list) else raw.get("data", []) if isinstance(raw, dict) else []
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for it in items:
        dt = _parse_dt(it.get("an_dt", ""))
        if dt and dt < cutoff:
            continue
        out.append({
            "date": it.get("an_dt"),
            "category": it.get("desc"),
            "text": (it.get("attchmntText") or "").strip(),
            "attachment": it.get("attchmntFile"),
        })
    return out


# ── Insider / SAST-PIT (~Form 4) ──────────────────────────────────────────────
def get_insider_trades(symbol: str, days: int = 180) -> list[dict]:
    sym = bare_symbol(symbol)
    url = f"https://www.nseindia.com/api/corporates-pit?index=equities&symbol={sym}"
    raw = nsefetch(url)
    rows = raw.get("data", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for r in rows:
        dt = _parse_dt(r.get("date") or r.get("anndt") or r.get("intimDt") or "")
        if dt and dt < cutoff:
            continue
        out.append({
            "date": r.get("date") or r.get("anndt") or r.get("intimDt"),
            "acquirer": r.get("acqName"),
            "category": r.get("personCategory"),
            "buy_sell": r.get("tdpTransactionType") or r.get("acqMode"),
            "shares": r.get("secAcq"),
            "value": r.get("secVal"),
            "sec_type": r.get("secType"),
        })
    return out


# ── Events / results calendar (~earnings calendar) ────────────────────────────
def get_events(symbol: str, upcoming_only: bool = True) -> list[dict]:
    sym = bare_symbol(symbol)
    url = f"https://www.nseindia.com/api/event-calendar?index=equities&symbol={sym}"
    raw = nsefetch(url)
    rows = raw if isinstance(raw, list) else raw.get("data", []) if isinstance(raw, dict) else []
    now = datetime.now()
    out = []
    for r in rows:
        dt = _parse_dt(r.get("date", ""))
        if upcoming_only and dt and dt < now:
            continue
        out.append({"date": r.get("date"), "purpose": r.get("purpose"),
                    "detail": r.get("bm_desc")})
    return out


# ── F&O securities-in-ban (~crowding / short pressure) ────────────────────────
def get_fno_ban_list() -> dict:
    r = requests.get(_BAN_CSV, headers=_HDR, timeout=20)
    r.raise_for_status()
    text = r.text
    trade_date = None
    syms: list[str] = []
    for line in io.StringIO(text):
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("securities in ban"):
            trade_date = line.split("Trade Date")[-1].strip(": ").strip()
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit():
            syms.append(parts[1].upper())
    return {"trade_date": trade_date, "symbols": syms}


def is_in_fno_ban(symbol: str, ban: dict | None = None) -> bool:
    ban = ban or get_fno_ban_list()
    return bare_symbol(symbol) in set(ban.get("symbols", []))


# ── demo / smoke test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    HOLDINGS = ["BEL", "HAL", "BDL", "HBLENGINE", "TATAPOWER", "PFC",
                "GMMPFAUDLR", "AVANTEL", "APOLLO", "RVNL"]

    print("="*72, "\nF&O SECURITIES-IN-BAN (today)\n", "="*72, sep="")
    ban = get_fno_ban_list()
    print("trade_date:", ban["trade_date"], "| count:", len(ban["symbols"]))
    held_banned = [s for s in HOLDINGS if s in set(ban["symbols"])]
    print("YOUR holdings currently in ban:", held_banned or "none")

    for sym in HOLDINGS[:4]:
        print("\n" + "="*72, f"\n{sym}\n", "="*72, sep="")
        ann = get_announcements(sym, days=45)
        print(f"announcements (45d): {len(ann)}")
        for a in ann[:3]:
            print(f"  [{a['date']}] {a['category']}: {(a['text'] or '')[:90]}")
        ins = get_insider_trades(sym, days=180)
        print(f"insider/SAST trades (180d): {len(ins)}")
        for t in ins[:3]:
            print(f"  [{t['date']}] {t['acquirer']} | {t['buy_sell']} | {t['shares']} | {t['category']}")
        ev = get_events(sym)
        print(f"upcoming events: {len(ev)}")
        for e in ev[:2]:
            print(f"  [{e['date']}] {e['purpose']}")
