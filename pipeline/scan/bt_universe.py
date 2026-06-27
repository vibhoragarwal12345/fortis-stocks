"""
SHADOW backtest universe builder -- SURVIVORSHIP-INCLUSIVE, point-in-time.
==========================================================================
Builds the historical US common-stock universe from Tiingo's supported_tickers
list, which carries per-ticker startDate/endDate -- so DELISTED / acquired /
bankrupt names are present with the date they stopped trading. This is the
anti-survivorship foundation: the universe "as it existed on date T" =
{ tickers with startDate <= T and (endDate is null or endDate >= T) }.

Filters to plausible COMMON stock (drops warrants/units/rights/preferreds via
ticker shape; Tiingo assetType already excludes ETFs). Heuristic, not perfect.

Output: pipeline/scan/data/bt_universe.csv  (ticker, exchange, start, end, delisted)
Local/shadow only; touches no live table; one free static download (cached).

    python pipeline/scan/bt_universe.py
"""

import csv
import io
import re
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests

SRC = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"
CACHE = Path(__file__).resolve().parent / "data" / "_bt_cache" / "supported_tickers.csv"
OUT = Path(__file__).resolve().parent / "data" / "bt_universe.csv"
US_EXCH = {"NYSE", "NASDAQ", "AMEX", "NYSE MKT", "NYSE ARCA", "BATS"}
# common-stock ticker shape: 1-5 letters, no '.'/'-'; drop 5-char warrant/unit/right suffixes
COMMON = re.compile(r"^[A-Z]{1,5}$")
SUFFIX_JUNK = re.compile(r"^[A-Z]{1,4}(W|U|R)$")  # 5-char WARRANT/UNIT/RIGHT-ish


def _load_rows() -> list[dict]:
    if CACHE.exists():
        return list(csv.DictReader(CACHE.open(encoding="utf-8")))
    print("downloading Tiingo supported_tickers (free static file) ...")
    r = requests.get(SRC, timeout=180)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    rows = list(csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), "utf-8")))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    return rows


def _is_common(t: str) -> bool:
    return bool(COMMON.match(t)) and not (len(t) == 5 and SUFFIX_JUNK.match(t))


def build() -> None:
    rows = _load_rows()
    # Tiingo sets endDate = the ticker's LAST DATA date (a day or two ago for
    # ACTIVE names), not null. So "delisted" = endDate well before the data's
    # latest date. Use a 7-day cutoff off the file's max endDate.
    max_end = max((x.get("endDate", "")[:10] for x in rows if x.get("endDate")),
                  default=date.today().isoformat())
    cutoff = (date.fromisoformat(max_end) - timedelta(days=7)).isoformat()
    keep = []
    for x in rows:
        t = (x.get("ticker") or "").upper()
        if (x.get("exchange") in US_EXCH and x.get("assetType") == "Stock"
                and x.get("priceCurrency") == "USD" and x.get("startDate")
                and _is_common(t)):
            end = (x.get("endDate") or "")[:10]
            keep.append({"ticker": t, "exchange": x["exchange"],
                         "start": x["startDate"][:10], "end": end,
                         "delisted": "1" if (end and end < cutoff) else "0"})
    # de-dup ticker (keep the longest-lived listing)
    best: dict[str, dict] = {}
    for r in keep:
        cur = best.get(r["ticker"])
        if cur is None or (r["end"] or "9999") > (cur["end"] or "9999"):
            best[r["ticker"]] = r
    uni = sorted(best.values(), key=lambda r: r["ticker"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "exchange", "start", "end", "delisted"])
        w.writeheader(); w.writerows(uni)

    delisted = [r for r in uni if r["delisted"] == "1"]
    de_window = [r for r in delisted if r["end"] >= "2025-01-01"]
    print(f"\nUS common-stock universe (survivorship-inclusive): {len(uni)} tickers")
    print(f"  active today: {sum(1 for r in uni if r['delisted']=='0')}  |  "
          f"delisted ever: {len(delisted)}  |  delisted since 2025-01-01: {len(de_window)}")
    for d in ("2025-01-15", "2025-07-01", "2026-01-02", "2026-06-01"):
        live = [r for r in uni if r["start"] <= d and (not r["end"] or r["end"] >= d)]
        print(f"  point-in-time universe on {d}: {len(live)} names")
    print("  sample delisted-in-window:",
          [(r["ticker"], r["end"]) for r in de_window[:8]])
    print(f"\nwrote -> {OUT}")


if __name__ == "__main__":
    sys.exit(build())
