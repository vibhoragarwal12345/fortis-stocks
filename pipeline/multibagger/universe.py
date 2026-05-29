"""
Multibagger Universe Builder
============================

Builds a broader, small/micro-cap-skewed universe distinct from the daily
S&P 1500 pipeline. Sources are picked for AUTHORITY and STABILITY:

  1. NASDAQ screener API               -- all US-listed common stocks across
                                          NYSE / NASDAQ / AMEX with
                                          marketCap + sector + ipoyear in
                                          one JSON call. ~7000 rows.
  2. Finnhub /calendar/ipo (last 3 y)  -- recent IPOs (some land in NASDAQ
                                          screener with marketCap=0, this
                                          backfills name + sector).

iShares CSVs (IWM / IJR) were the first choice but BlackRock gates direct
CSV access server-side -- the URL returns the consent landing page rather
than the holdings file. The NASDAQ screener gives broader coverage anyway
(captures micro-caps below the R2000 floor that the spec explicitly wants).

Each source contributes ticker + (where available) market_cap and sector.
The final filter keeps names with $50M <= market_cap <= $10B per the spec.

Refresh cadence: monthly.

CLI
    python pipeline/multibagger/universe.py
    python pipeline/multibagger/universe.py --sample 200    # debug subset
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import FINNHUB_API_KEY  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH = DATA_DIR / "mb_universe.csv"

MIN_MARKET_CAP = 50_000_000      # $50M -- below this is too illiquid/risky
MAX_MARKET_CAP = 10_000_000_000  # $10B -- above this is too big for multibagger math

NASDAQ_SCREENER_URL = (
    "https://api.nasdaq.com/api/screener/stocks"
    "?tableonly=true&limit=25000&download=true"
)

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/json,text/plain,*/*",
}


# ══════════════════════════════════════════════════════════════════════════════
# NASDAQ screener  (covers NYSE / NASDAQ / AMEX common stocks)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_dollar(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("$", "")
    if not s or s == "-" or s.lower() == "nan":
        return None
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def _fetch_nasdaq_screener() -> pd.DataFrame:
    log.info("Fetching NASDAQ screener (full US-listed universe)…")
    r = requests.get(NASDAQ_SCREENER_URL, headers=_HTTP_HEADERS, timeout=60)
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") or {}
    rows = data.get("rows") or data.get("table", {}).get("rows") or []
    if not rows:
        raise RuntimeError("NASDAQ screener returned no rows.")

    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "ticker":       df["symbol"].astype(str).str.strip().str.upper(),
        "name":         df["name"].astype(str).str.strip(),
        "sector":       df.get("sector",   pd.Series([""] * len(df))).astype(str).str.strip(),
        "industry":     df.get("industry", pd.Series([""] * len(df))).astype(str).str.strip(),
        "market_value": df["marketCap"].apply(_parse_dollar),
        "ipo_year":     pd.to_numeric(df.get("ipoyear"), errors="coerce"),
        "country":      df.get("country", pd.Series([""] * len(df))).astype(str).str.strip(),
        "source":       "NASDAQ screener",
    })
    # Keep only common-stock-style tickers (no ETFs, units, warrants, etc.).
    bad = out["ticker"].str.contains(r"[^A-Z\-./]", regex=True) | out["ticker"].isin(["", "-"])
    out = out[~bad]
    # The screener already restricts to US-domiciled / US-listed names.
    log.info("  NASDAQ screener: %d rows", len(out))
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Finnhub — supplemental
# ══════════════════════════════════════════════════════════════════════════════

def _finnhub_get(path: str, **params) -> list | dict | None:
    if not FINNHUB_API_KEY:
        return None
    params = {**params, "token": FINNHUB_API_KEY}
    url = f"https://finnhub.io/api/v1/{path}"
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=_HTTP_HEADERS, timeout=30)
            if r.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            log.warning("Finnhub %s failed (attempt %d): %s", path, attempt + 1, exc)
            time.sleep(1 + attempt)
    return None


def _fetch_recent_ipos(years_back: int = 3) -> pd.DataFrame:
    """Finnhub /calendar/ipo, walked month-by-month back to the cutoff."""
    end = date.today()
    start = end - timedelta(days=365 * years_back)
    log.info("Fetching IPO calendar %s..%s (Finnhub)…", start, end)
    rows: list[dict] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=90), end)
        data = _finnhub_get(
            "calendar/ipo",
            **{"from": chunk_start.isoformat(), "to": chunk_end.isoformat()},
        )
        if isinstance(data, dict):
            for ipo in data.get("ipoCalendar") or []:
                sym = (ipo.get("symbol") or "").strip().upper()
                if sym and sym.isalpha():
                    rows.append({
                        "ticker":   sym,
                        "name":     ipo.get("name") or "",
                        "sector":   "",
                        "market_value": _parse_dollar(ipo.get("totalSharesValue")),
                        "ipo_date": ipo.get("date"),
                        "source":   "Finnhub IPO",
                    })
        chunk_start = chunk_end + timedelta(days=1)
    df = pd.DataFrame(rows).drop_duplicates(subset=["ticker"], keep="first")
    log.info("  %d recent IPOs", len(df))
    return df


def _enrich_market_caps(df: pd.DataFrame, max_calls: int) -> pd.DataFrame:
    """For rows missing market_value, fetch Finnhub /stock/profile2 (1 call
    each). Bounded by max_calls to keep total time reasonable."""
    if not FINNHUB_API_KEY:
        return df
    need = df[df["market_value"].isna() | (df["market_value"] <= 0)].head(max_calls)
    if need.empty:
        return df
    log.info("Enriching market cap for %d tickers (Finnhub profile2)…", len(need))
    caps: dict[str, float | None] = {}
    for i, ticker in enumerate(need["ticker"].tolist(), 1):
        prof = _finnhub_get("stock/profile2", symbol=ticker)
        if isinstance(prof, dict):
            mc = prof.get("marketCapitalization")
            if isinstance(mc, (int, float)):
                # profile2 returns market cap in *millions of USD*.
                caps[ticker] = float(mc) * 1_000_000
        if i % 25 == 0:
            log.info("  …%d/%d", i, len(need))
        # Finnhub free tier is ~60 calls/min. Stay well under.
        time.sleep(1.05)
    mask = df["ticker"].isin(caps)
    df.loc[mask, "market_value"] = df.loc[mask, "ticker"].map(caps)
    return df


# ══════════════════════════════════════════════════════════════════════════════
# Build
# ══════════════════════════════════════════════════════════════════════════════

def build_universe(sample: int | None = None, enrich_caps: int = 0) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    try:
        parts.append(_fetch_nasdaq_screener())
    except Exception as exc:
        log.warning("NASDAQ screener fetch failed: %s", exc)

    if FINNHUB_API_KEY:
        try:
            ipos = _fetch_recent_ipos(years_back=3)
            if not ipos.empty:
                parts.append(ipos)
        except Exception as exc:
            log.warning("IPO calendar fetch failed: %s", exc)
    else:
        log.warning("FINNHUB_API_KEY missing -- skipping IPO calendar")

    if not parts:
        raise RuntimeError("No universe sources returned data -- aborting.")

    merged = pd.concat(parts, ignore_index=True)
    for col in ("industry", "ipo_year", "country"):
        if col not in merged.columns:
            merged[col] = pd.NA
    merged["market_value"] = pd.to_numeric(merged["market_value"], errors="coerce")
    agg = (
        merged.sort_values("market_value", ascending=False, na_position="last")
              .groupby("ticker", as_index=False)
              .agg(name=("name", lambda s: next((x for x in s if x), "")),
                   sector=("sector", lambda s: next((x for x in s if x), "")),
                   industry=("industry", lambda s: next((x for x in s if isinstance(x, str) and x), "")),
                   market_value=("market_value", "max"),
                   ipo_year=("ipo_year", "max"),
                   country=("country", lambda s: next((x for x in s if isinstance(x, str) and x), "")),
                   sources=("source", lambda s: ",".join(sorted(set(s)))))
    )

    if enrich_caps > 0:
        agg = _enrich_market_caps(agg, max_calls=enrich_caps)

    # Apply spec filters.
    before = len(agg)
    filt = agg[
        (agg["market_value"].fillna(0) >= MIN_MARKET_CAP)
        & (agg["market_value"].fillna(0) <= MAX_MARKET_CAP)
    ].copy()
    log.info(
        "Filter $%dM..$%dB -> %d / %d kept",
        MIN_MARKET_CAP // 1_000_000,
        MAX_MARKET_CAP // 1_000_000_000,
        len(filt),
        before,
    )

    filt = filt.sort_values("ticker").reset_index(drop=True)
    if sample:
        filt = filt.head(sample)
    return filt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None,
                    help="Trim to first N tickers after sort (debug only).")
    ap.add_argument("--enrich-caps", type=int, default=0,
                    help="Max Finnhub profile2 calls to fill missing market caps.")
    args = ap.parse_args()

    universe = build_universe(sample=args.sample, enrich_caps=args.enrich_caps)
    universe.to_csv(OUTPUT_PATH, index=False)
    log.info("Wrote %d tickers to %s", len(universe), OUTPUT_PATH)
    log.info("Top by market cap:\n%s",
             universe.sort_values("market_value", ascending=False).head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
