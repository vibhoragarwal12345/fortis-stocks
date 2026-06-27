"""
One-time driver: fetch & cache the in-window delisted names (the survivorship
set). yfinance first (free), Tiingo_2 for the ~319 it lacks, THROTTLED to the
free 50/hr limit so it never 429-blocks. Resumable: cached tickers are skipped.
Run in the background -- it caches over a few hours.

    python pipeline/scan/bt_fetch_deaths.py
"""
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scan import bt_prices  # noqa: E402

ROWS = list(csv.DictReader(
    (Path(__file__).resolve().parent / "data" / "bt_universe.csv").open(encoding="utf-8")))
DEATHS = [r["ticker"] for r in ROWS if r["delisted"] == "1" and r["end"] >= "2025-01-01"]

print(f"deaths to fetch: {len(DEATHS)}", flush=True)
ok = miss = tiingo = 0
for i, t in enumerate(DEATHS, 1):
    df = bt_prices.get(t, start="2024-06-01", end="2026-06-25")
    src = (df.attrs.get("source", "") if df is not None else "none")
    if df is None:
        miss += 1
    else:
        ok += 1
    if src.startswith("tiingo"):
        tiingo += 1
    if i % 20 == 0 or df is None:
        print(f"  [{i}/{len(DEATHS)}] {t:8} src={src:9} ok={ok} miss={miss} tiingo_calls={tiingo}",
              flush=True)
    if src.startswith("tiingo"):
        time.sleep(72)   # respect Tiingo free tier (~50 req/hr)

print(f"DONE: ok={ok} miss={miss} tiingo_calls={tiingo} of {len(DEATHS)} deaths", flush=True)
