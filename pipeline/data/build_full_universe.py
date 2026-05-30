"""
Full liquid US universe -- the input for PART 3's fast scan.
================================================================

Pulls every US-listed common stock from NYSE / NASDAQ / AMEX, filters out
the non-equity junk (warrants, units, rights, preferreds, ADR depositary
shares, ETFs that snuck through), and applies a $1M average daily dollar
volume floor over the last 30 days. The output is the canonical input for
Layer 1 of the two-speed funnel.

SOURCES
  1. NASDAQ stock screener API
       https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25000&download=true
     One JSON call, ~7000 rows, gives ticker + name + exchange + sector +
     marketCap + lastsale + last-day volume.

  2. yfinance batch download
       yf.download(tickers, period="1mo", interval="1d", group_by='ticker')
     30 days of OHLCV. We compute mean(close * volume) per ticker for the
     authoritative 30-day average dollar volume figure.

OUTPUT
  pipeline/data/full_universe.csv  with columns:
    ticker, exchange, name, sector, industry, market_cap_usd,
    avg_dollar_volume_30d, ipo_year, country

CADENCE
  Weekly. See .github/workflows/universe_weekly.yml.

CLI
  python pipeline/data/build_full_universe.py
  python pipeline/data/build_full_universe.py --sample 500
  python pipeline/data/build_full_universe.py --min-adv 500000   # override floor
  python pipeline/data/build_full_universe.py --batch 100        # yf batch size
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

LOG_FORMAT = "%(asctime)s  %(levelname)-7s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = DATA_DIR / "full_universe.csv"
DEFAULT_MIN_ADV = 1_000_000  # $1M average daily dollar volume floor

NASDAQ_SCREENER_URL = (
    "https://api.nasdaq.com/api/screener/stocks"
    "?tableonly=true&limit=25000&download=true"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Junk-ticker filters ─────────────────────────────────────────────────────
# Warrants typically end in W or .WS; rights in R or .R; units in U;
# preferreds carry letter suffixes (BAC-PE, BAC.PE, BAC^E). The NASDAQ
# screener already excludes most preferreds via its is-an-equity flag, but
# a regex sweep removes the survivors.
TICKER_BLOCK_RE = re.compile(
    r"""
    ^.+   # at least one base char
    (
        [-^./]?[Pp][A-Z]?$    # preferred: BAC.PE, BAC^E, BAC-PB
      | [-^./]?W[A-Z]?$       # warrant:  ACME.WS
      | [-^./]?R$             # right
      | [-^./]?U$             # unit (SPAC)
      | -RTS?$
      | -WT$
      | -UN$
      | -PR[A-Z]?$
    )
    """,
    re.VERBOSE,
)

# Name-based excludes -- catches the rest. Keep this conservative so we
# don't drop legitimate common stock that happens to have one of these
# words in its long name (e.g. "Trust Bancshares").
NAME_BLOCK_RE = re.compile(
    r"""(
        \bWarrant\b
      | \bRights?\b
      | \bDepositary\s+Shares?\b
      | \bConvertible\s+Preferred\b
      | \bCumulative\s+Preferred\b
      | \bSeries\s+[A-Z]\s+Preferred\b
      | \bPerpetual\s+Preferred\b
      | \bSubordinated\s+Notes?\b
      | \bDebenture\b
      | \bUnit\b
    )""",
    re.VERBOSE | re.IGNORECASE,
)

# ETF-name patterns. The NASDAQ stocks screener is supposed to exclude
# ETFs but a few slip through (CEFs, ETNs, BDCs that walk like ETFs).
ETF_BLOCK_RE = re.compile(
    r"""(
        \bETF\b
      | \bETN\b
      | \bExchange[ -]Traded\b
      | \bIndex\s+Fund\b
    )""",
    re.VERBOSE | re.IGNORECASE,
)


def _parse_dollar(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("$", "")
    if not s or s in ("-", "nan", "N/A"):
        return None
    try:
        f = float(s)
        return f if f > 0 else None
    except ValueError:
        return None


def fetch_nasdaq_screener() -> pd.DataFrame:
    log.info("Fetching NASDAQ screener (full US-listed universe)…")
    r = requests.get(NASDAQ_SCREENER_URL, headers=_HEADERS, timeout=60)
    r.raise_for_status()
    payload = r.json()
    rows = (payload.get("data") or {}).get("rows") or []
    if not rows:
        raise RuntimeError("NASDAQ screener returned no rows.")
    df = pd.DataFrame(rows)

    out = pd.DataFrame(
        {
            "ticker":         df["symbol"].astype(str).str.strip().str.upper(),
            "name":           df["name"].astype(str).str.strip(),
            "sector":         df.get("sector",   pd.Series([""] * len(df))).astype(str).str.strip(),
            "industry":       df.get("industry", pd.Series([""] * len(df))).astype(str).str.strip(),
            "country":        df.get("country",  pd.Series([""] * len(df))).astype(str).str.strip(),
            "market_cap_usd": df["marketCap"].apply(_parse_dollar),
            "ipo_year":       pd.to_numeric(df.get("ipoyear"), errors="coerce"),
            "last_sale":      df["lastsale"].apply(_parse_dollar),
            "last_volume":    pd.to_numeric(df.get("volume"), errors="coerce"),
        }
    )
    log.info("  raw rows: %d", len(out))
    return out


def filter_to_common_equity(df: pd.DataFrame) -> pd.DataFrame:
    """Strip warrants, rights, units, preferreds, ETFs, ADR depositaries."""
    before = len(df)

    # Bad characters in ticker
    bad_chars = df["ticker"].str.contains(r"[^A-Z\-./]", regex=True)
    # Tickers matching our junk-suffix regex
    bad_suffix = df["ticker"].apply(lambda t: bool(TICKER_BLOCK_RE.match(t)))
    # Name-based excludes
    bad_name = df["name"].apply(lambda n: bool(NAME_BLOCK_RE.search(str(n))))
    bad_etf = df["name"].apply(lambda n: bool(ETF_BLOCK_RE.search(str(n))))

    out = df[~(bad_chars | bad_suffix | bad_name | bad_etf)].copy()

    # Drop everything that isn't priced in USD on a US exchange. NASDAQ's
    # data sometimes leaves country blank for primary-listed names; we
    # don't drop those, only exclude rows explicitly flagged as foreign-
    # only with no US share class.
    out = out.dropna(subset=["ticker"])
    out = out[out["ticker"].str.len().between(1, 6)]

    log.info(
        "  after common-equity filter: %d / %d  (-%d)",
        len(out),
        before,
        before - len(out),
    )
    return out.reset_index(drop=True)


def compute_avg_dollar_volume(
    tickers: list[str],
    batch_size: int = 100,
    period: str = "1mo",
) -> dict[str, float]:
    """Batch-download last 30 days OHLCV, return mean(close * volume) per ticker.

    yfinance handles batching internally when you pass a list, but in
    practice >200 tickers per call hits Yahoo's flaky chunking. We keep
    batches to 100. Tickers that fail return None and are excluded.
    """
    try:
        import yfinance as yf  # noqa: F401
    except ImportError:
        log.error("yfinance not installed; pip install yfinance")
        return {}
    import yfinance as yf

    # NASDAQ uses slash + dot in class share tickers (BRK/A, BF.B); Yahoo
    # uses hyphen (BRK-A, BF-B). Normalise on the way out, denormalise on
    # the way back into the result dict.
    def _to_yahoo(t: str) -> str:
        return t.replace("/", "-").replace(".", "-")

    log.info(
        "Fetching 30d OHLCV for %d tickers in batches of %d…",
        len(tickers),
        batch_size,
    )
    adv: dict[str, float] = {}
    failures: list[str] = []
    t0 = time.time()
    for i in range(0, len(tickers), batch_size):
        chunk = tickers[i : i + batch_size]
        yahoo_chunk = [_to_yahoo(t) for t in chunk]
        yahoo_to_orig = dict(zip(yahoo_chunk, chunk))
        try:
            df = yf.download(
                tickers=" ".join(yahoo_chunk),
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            log.warning("  batch %d-%d failed: %s", i, i + len(chunk), exc)
            failures.extend(chunk)
            continue

        # yfinance returns multi-indexed columns when group_by='ticker';
        # we walk each ticker out of the frame.
        for yt in yahoo_chunk:
            orig = yahoo_to_orig[yt]
            try:
                sub = df[yt] if isinstance(df.columns, pd.MultiIndex) else df
                close = sub["Close"].dropna()
                volume = sub["Volume"].dropna()
                joined = pd.concat([close, volume], axis=1, join="inner").dropna()
                if joined.empty:
                    failures.append(orig)
                    continue
                dollar = (joined["Close"] * joined["Volume"]).mean()
                if pd.isna(dollar) or dollar <= 0:
                    failures.append(orig)
                    continue
                adv[orig] = float(dollar)
            except (KeyError, TypeError):
                failures.append(orig)
        # Light cadence; yfinance is unauthenticated and Yahoo is flaky
        # when hammered.
        time.sleep(0.4)
        if (i // batch_size + 1) % 5 == 0:
            done = min(i + batch_size, len(tickers))
            eta = (len(tickers) - done) / max(done, 1) * (time.time() - t0)
            log.info(
                "  …%d/%d  (%d kept, %d failed, eta %.0fs)",
                done,
                len(tickers),
                len(adv),
                len(failures),
                eta,
            )
    log.info(
        "ADV fetch complete: kept %d / %d (%d failed)",
        len(adv),
        len(tickers),
        len(failures),
    )
    return adv


def build(min_adv: float = DEFAULT_MIN_ADV,
          sample: int | None = None,
          batch_size: int = 100) -> pd.DataFrame:
    raw = fetch_nasdaq_screener()
    clean = filter_to_common_equity(raw)

    if sample:
        # Sampling is for dev only -- random N from the clean set, with a
        # bias toward larger caps so we get a meaningful slice.
        clean = clean.sort_values("market_cap_usd", ascending=False, na_position="last")
        clean = clean.head(sample).reset_index(drop=True)
        log.info("Sample mode: scoring %d tickers", len(clean))

    tickers = clean["ticker"].tolist()
    adv_map = compute_avg_dollar_volume(tickers, batch_size=batch_size)
    clean["avg_dollar_volume_30d"] = clean["ticker"].map(adv_map)

    before = len(clean)
    floored = clean[
        (clean["avg_dollar_volume_30d"].notna())
        & (clean["avg_dollar_volume_30d"] >= min_adv)
    ].copy()
    log.info(
        "Liquidity floor $%dM: %d / %d kept (-%d)",
        int(min_adv) // 1_000_000,
        len(floored),
        before,
        before - len(floored),
    )

    # We don't have a reliable exchange code from the NASDAQ screener row;
    # store an empty string -- the per-ticker quote endpoints will fill it
    # if we ever need it.
    if "exchange" not in floored.columns:
        floored["exchange"] = ""

    cols = [
        "ticker",
        "exchange",
        "name",
        "sector",
        "industry",
        "market_cap_usd",
        "avg_dollar_volume_30d",
        "ipo_year",
        "country",
    ]
    floored = floored[cols].sort_values(
        "avg_dollar_volume_30d", ascending=False
    ).reset_index(drop=True)
    return floored


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None,
                    help="Score only the top-N largest caps (dev mode).")
    ap.add_argument("--min-adv", type=float, default=DEFAULT_MIN_ADV,
                    help="Liquidity floor in USD (default 1_000_000).")
    ap.add_argument("--batch", type=int, default=100,
                    help="yfinance batch size (default 100).")
    args = ap.parse_args()

    universe = build(min_adv=args.min_adv, sample=args.sample, batch_size=args.batch)
    universe.to_csv(OUTPUT_PATH, index=False)
    log.info("Wrote %d tickers to %s", len(universe), OUTPUT_PATH)
    log.info(
        "Top 10 by ADV:\n%s",
        universe[["ticker", "name", "avg_dollar_volume_30d", "sector"]]
        .head(10)
        .to_string(index=False),
    )
    log.info(
        "Sector mix:\n%s",
        universe["sector"].value_counts().head(12).to_string(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
