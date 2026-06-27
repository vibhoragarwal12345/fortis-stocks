"""
Batch yfinance prefetch of SURVIVOR prices into the bt_prices cache (fast).
A RANDOM sample of the point-in-time Jan-2025 survivor universe (random ==
avoids today's-winner selection bias). yfinance only (no Tiingo). Skips cached.
The liquidity filter in bt_engine then carves the liquid universe per date.

    python pipeline/scan/bt_fetch_survivors.py [N=2500]
"""
import csv
import random
import sys
import warnings
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scan import bt_prices  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 2500
START, END = "2024-06-01", "2026-06-26"

ROWS = list(csv.DictReader(
    (Path(__file__).resolve().parent / "data" / "bt_universe.csv").open(encoding="utf-8")))
survivors = [r["ticker"] for r in ROWS if r["delisted"] == "0" and r["start"] <= "2025-01-02"]
random.Random(42).shuffle(survivors)
sample = survivors[:N]
want = [t for t in sample if not (bt_prices.CACHE / f"{t}.csv").exists()]
print(f"survivor universe (PIT Jan-2025): {len(survivors)}; sampled {len(sample)}; "
      f"to fetch (uncached): {len(want)}", flush=True)


def yh(t):
    return t.replace(".", "-")


CH, ok = 100, 0
for i in range(0, len(want), CH):
    chunk = want[i:i + CH]
    rev = {yh(t): t for t in chunk}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            data = yf.download([yh(t) for t in chunk], start=START, end=END, interval="1d",
                               group_by="ticker", auto_adjust=False, actions=True,
                               progress=False, threads=True)
    except Exception as e:
        print(f"  batch {i} failed: {e}", flush=True)
        continue
    multi = isinstance(data.columns, pd.MultiIndex)
    for yt, t in rev.items():
        try:
            sub = data[yt] if multi else data
        except KeyError:
            continue
        sub = sub.rename(columns=str.lower)
        if "close" not in sub.columns:
            continue
        out = pd.DataFrame(index=sub.index)
        for c in ("open", "high", "low", "close", "volume"):
            out[c] = sub.get(c)
        spl = sub.get("stock splits")
        out["split"] = (spl.replace(0, 1.0) if spl is not None else 1.0)
        out["dividend"] = sub.get("dividends", 0.0)
        out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
        out = out.dropna(subset=["close"])
        if len(out) >= 60:
            bt_prices._to_cache(t, out)
            ok += 1
    print(f"  [{min(i + CH, len(want))}/{len(want)}] cached_ok={ok}", flush=True)

print(f"DONE survivors: cached_ok={ok} of {len(want)}", flush=True)
