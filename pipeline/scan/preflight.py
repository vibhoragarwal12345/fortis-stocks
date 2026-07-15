"""Scan preflight — fail fast, not 25 minutes in.

Pattern absorbed from HKUDS/Vibe-Trading's startup preflight: check every
dependency the scan needs BEFORE burning workflow minutes. Non-critical
failures degrade with a warning; critical ones abort the scan with a clear
reason in market_scans.notes instead of a mid-run timeout.

Critical:   Supabase (nothing persists without it), yfinance (Layer 1 data)
Degradable: Finnhub (Layer-3 enrichment), LLM keys (dossiers thin out but
            Layer 1/2 + price bands still publish)
"""

from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)


def _check_supabase() -> tuple[bool, str]:
    try:
        from config import SUPABASE_SERVICE_KEY, SUPABASE_URL
        from supabase import create_client
        db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        db.table("market_scans").select("id").limit(1).execute()
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _check_yfinance() -> tuple[bool, str]:
    try:
        import yfinance as yf
        df = yf.download("SPY", period="5d", interval="1d", progress=False)
        if df is None or df.empty:
            return False, "SPY probe returned empty frame"
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _check_finnhub() -> tuple[bool, str]:
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        return False, "FINNHUB_API_KEY not set"
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={key}")
        with urllib.request.urlopen(req, timeout=10) as r:
            return (r.status == 200), f"http {r.status}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _check_llm_keys() -> tuple[bool, str]:
    present = [k for k in ("CEREBRAS_API_KEY", "GROQ_API_KEY",
                           "NVIDIA_API_KEY", "GEMINI_API_KEY")
               if os.environ.get(k)]
    return (len(present) > 0), f"{len(present)} provider key(s) present"


def run_preflight() -> tuple[bool, list[str]]:
    """Returns (ok_to_scan, notes). ok_to_scan False => abort before Layer 1."""
    t0 = time.time()
    checks = [
        ("supabase", _check_supabase, True),
        ("yfinance", _check_yfinance, True),
        ("finnhub", _check_finnhub, False),
        ("llm_keys", _check_llm_keys, False),
    ]
    notes: list[str] = []
    ok = True
    for name, fn, critical in checks:
        passed, detail = fn()
        tag = "OK " if passed else ("FAIL" if critical else "WARN")
        line = f"preflight {tag} {name}: {detail}"
        notes.append(line)
        (log.info if passed else log.warning)("%s", line)
        if critical and not passed:
            ok = False
    log.info("preflight complete in %.1fs -> %s", time.time() - t0,
             "GO" if ok else "ABORT")
    return ok, notes
