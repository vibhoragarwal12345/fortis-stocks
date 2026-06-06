"""
India source #4 -- F&O securities-in-ban (crowding / short-pressure proxy).
Partial counterpart to FINRA short interest. NSE publishes a daily CSV of
stocks where F&O open interest has breached 95% of market-wide position limits
(a sign of extreme crowding). Free, no key.
"""
from __future__ import annotations

import io
import warnings

warnings.filterwarnings("ignore")
import requests

from ._common import bare_symbol

_BAN_CSV = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
_HDR = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}


def get_fno_ban_list() -> dict:
    """Returns {'trade_date': str, 'symbols': [..]} for today's ban list."""
    r = requests.get(_BAN_CSV, headers=_HDR, timeout=20)
    r.raise_for_status()
    trade_date, syms = None, []
    for line in io.StringIO(r.text):
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
    ban = ban if ban is not None else get_fno_ban_list()
    return bare_symbol(symbol) in set(ban.get("symbols", []))


if __name__ == "__main__":
    b = get_fno_ban_list()
    print("trade_date:", b["trade_date"], "| count:", len(b["symbols"]))
    print("symbols:", b["symbols"])
