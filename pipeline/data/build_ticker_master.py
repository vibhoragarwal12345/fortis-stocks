"""
Ticker Master Builder  (one-time / periodic job)
Builds pipeline/data/ticker_master.csv -- the authoritative ticker -> company
identity map that news_harvester's multi-stage resolution verifies against.

A ticker is included ONLY if it appears in BOTH authoritative sources:
  * SEC EDGAR company tickers   https://www.sec.gov/files/company_tickers.json
  * Finnhub US symbol list      /stock/symbol?exchange=US

The official company name is taken from SEC EDGAR (the legal filer name); the
exchange / MIC is taken from Finnhub. For each company a set of fuzzy-match
aliases is derived (name without corporate suffixes, leading words) so the
harvester's NER stage can match "Vistra" to "Vistra Corp".

Output columns (ticker_master.csv):
  ticker, company_name, cik, mic, exchange_context, aliases

exchange_context is the geography this listing belongs to ("US"); the harvester
rejects a US ticker when an article's dominant geography is foreign (e.g. a
Mumbai-datelined story tagged to NYSE:VST).

Usage:
    python pipeline/data/build_ticker_master.py
"""

import csv
import logging
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import FINNHUB_API_KEY  # noqa: E402

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FINNHUB_SYMBOL_URL = "https://finnhub.io/api/v1/stock/symbol"
# SEC requires a descriptive User-Agent on every request.
SEC_HEADERS = {"User-Agent": "Fortis Stock Intelligence pipeline (contact@fortis.example)"}
HTTP_TIMEOUT = 40

OUTPUT_PATH = Path(__file__).resolve().parent / "ticker_master.csv"

# Corporate suffixes / filler stripped when deriving fuzzy aliases.
_SUFFIXES = re.compile(
    r"\b(incorporated|inc|corporation|corp|company|cos?|ltd|limited|plc|llc|"
    r"l\.?p|lp|holdings?|group|trust|the|s\.?a|n\.?v|a\.?g|se|class\s+[a-c]|"
    r"common\s+stock|ordinary\s+shares?|adr|ads)\b", re.IGNORECASE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Authoritative sources
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_sec() -> dict[str, dict]:
    """SEC EDGAR company tickers -> {TICKER: {cik, name}}."""
    resp = requests.get(SEC_TICKERS_URL, headers=SEC_HEADERS, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    out: dict[str, dict] = {}
    for row in resp.json().values():
        sym = str(row.get("ticker", "")).upper().strip()
        if sym:
            out[sym] = {"cik": str(row.get("cik_str", "")).zfill(10),
                        "name": str(row.get("title", "")).strip()}
    log.info("SEC EDGAR: %d tickers", len(out))
    return out


def _fetch_finnhub() -> dict[str, dict]:
    """Finnhub US symbol list -> {TICKER: {description, mic, type}}."""
    if not FINNHUB_API_KEY:
        raise RuntimeError("FINNHUB_API_KEY not set -- required to build the master")
    resp = requests.get(FINNHUB_SYMBOL_URL,
                        params={"exchange": "US", "token": FINNHUB_API_KEY},
                        timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    out: dict[str, dict] = {}
    for row in resp.json() or []:
        sym = str(row.get("symbol", "")).upper().strip()
        if sym:
            out[sym] = {"description": str(row.get("description", "")).strip(),
                        "mic": str(row.get("mic", "")).strip(),
                        "type": str(row.get("type", "")).strip()}
    log.info("Finnhub US: %d symbols", len(out))
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Alias derivation
# ══════════════════════════════════════════════════════════════════════════════

def _aliases(name: str) -> list[str]:
    """Fuzzy-match aliases for a company name (for NER verification)."""
    low = name.lower()
    cleaned = _SUFFIXES.sub(" ", low)
    cleaned = re.sub(r"[^a-z0-9 ]", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    out = {low.strip(), cleaned}
    words = cleaned.split()
    if words:
        out.add(words[0])                       # leading word ("vistra")
    if len(words) >= 2:
        out.add(" ".join(words[:2]))            # leading two words
    return sorted(a for a in out if len(a) >= 2)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run() -> None:
    log.info("Building ticker master from SEC EDGAR + Finnhub...")
    sec = _fetch_sec()
    finnhub = _fetch_finnhub()

    common = sorted(set(sec) & set(finnhub))
    log.info("Cross-reference: %d tickers present in BOTH sources "
             "(SEC %d, Finnhub %d)", len(common), len(sec), len(finnhub))

    rows = []
    for sym in common:
        name = sec[sym]["name"] or finnhub[sym]["description"]
        if not name:
            continue
        rows.append({
            "ticker":           sym,
            "company_name":     name,
            "cik":              sec[sym]["cik"],
            "mic":              finnhub[sym]["mic"],
            "exchange_context": "US",
            "aliases":          "|".join(_aliases(name)),
        })

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ticker", "company_name", "cik",
                                                "mic", "exchange_context", "aliases"])
        writer.writeheader()
        writer.writerows(rows)

    log.info("Wrote %d verified tickers to %s", len(rows), OUTPUT_PATH)
    # Spot-check the ticker that triggered this work.
    vst = next((r for r in rows if r["ticker"] == "VST"), None)
    if vst:
        log.info("Spot-check VST -> %s (%s, aliases: %s)",
                 vst["company_name"], vst["mic"], vst["aliases"])


if __name__ == "__main__":
    run()
