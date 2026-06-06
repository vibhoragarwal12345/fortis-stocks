"""Shared helpers for the India (NSE) data-layer ingestors."""
from __future__ import annotations

import re
from datetime import datetime

_SUFFIX = re.compile(r"\b(limited|ltd|ltd\.)\b", re.I)
_NONALNUM = re.compile(r"[^a-z0-9 ]+")


def bare_symbol(ticker: str) -> str:
    """'BEL.NS' / 'BEL.BO' / 'bel' -> 'BEL'."""
    return ticker.split(".")[0].strip().upper()


def parse_dt(s: str | None):
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _norm(name: str | None) -> set[str]:
    if not name:
        return set()
    n = _SUFFIX.sub(" ", name.lower())
    n = _NONALNUM.sub(" ", n)
    return {t for t in n.split() if len(t) > 2}


def verify_company(record_name: str | None, expected_name: str | None) -> bool:
    """Ticker-resolution discipline: confirm a fetched record actually belongs
    to the company we asked for. True if no expectation given (caller opted out)
    or the names share their significant tokens -- guards silent mis-resolution.
    """
    if not expected_name:
        return True
    r, e = _norm(record_name), _norm(expected_name)
    if not r or not e:
        return False
    overlap = len(r & e) / len(e)
    return overlap >= 0.5
