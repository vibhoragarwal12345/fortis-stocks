"""
Downloads the S&P 1500 constituent tickers from Wikipedia and writes them
to pipeline/data/tickers.csv.

Usage:
    python pipeline/data/sp1500.py

Requires: pandas, requests, lxml  (all in requirements.txt)
"""

from pathlib import Path

import pandas as pd

OUTPUT_PATH = Path(__file__).resolve().parent / "tickers.csv"

# Wikipedia table column names vary slightly per index
SOURCES = [
    {
        "name": "S&P 500",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "table_index": 0,
        "column": "Symbol",
    },
    {
        "name": "S&P MidCap 400",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
        "table_index": 0,
        "column": "Ticker",
    },
    {
        "name": "S&P SmallCap 600",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
        "table_index": 0,
        "column": "Ticker",
    },
]


def _fetch_tickers(source: dict) -> list[str]:
    tables = pd.read_html(source["url"], attrs={"id": "constituents"})
    df = tables[0]

    # Find the ticker column regardless of exact capitalisation
    col = next(
        (c for c in df.columns if c.strip().lower() == source["column"].lower()),
        None,
    )
    if col is None:
        # Fallback: try the first column
        col = df.columns[0]

    tickers = df[col].dropna().astype(str).str.strip().tolist()
    # yfinance uses hyphens; Wikipedia uses dots (e.g. BRK.B → BRK-B)
    tickers = [t.replace(".", "-") for t in tickers]
    return tickers


def build_universe() -> None:
    all_tickers: list[str] = []

    for source in SOURCES:
        print(f"Fetching {source['name']}…", end=" ", flush=True)
        try:
            tickers = _fetch_tickers(source)
            all_tickers.extend(tickers)
            print(f"{len(tickers)} tickers")
        except Exception as exc:
            print(f"FAILED — {exc}")

    unique = sorted(set(all_tickers))
    pd.DataFrame({"ticker": unique}).to_csv(OUTPUT_PATH, index=False)
    print(f"\nWrote {len(unique)} unique tickers to {OUTPUT_PATH}")


if __name__ == "__main__":
    build_universe()
