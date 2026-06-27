"""
SHADOW backtest price fetcher -- survivorship-honest, point-in-time-friendly.
============================================================================
Stores RAW (UNADJUSTED) OHLCV + split/dividend actions per ticker, so the
backtest can adjust CAUSALLY: signals at date T use only splits up to T (no
adjusted-close leakage), while forward returns over (T, T+N] apply only the
splits inside that window. Back-adjusted close is NOT stored as the signal
price for exactly this reason.

ROUTING (yfinance primary to spare the pilot's Tiingo quota):
  1. yfinance (free, fast) -- survivors and the ~21% of deaths Yahoo still serves.
  2. Tiingo (TIINGO_API_KEY_2 dedicated backtest key FIRST, primary only as a
     last resort) -- ONLY for the delisted names yfinance lacks. One-time,
     cached forever, so the pilot's primary Tiingo quota is spared.

Per-ticker CSV cache; never re-fetches a cached name.
"""

import os
import sys
import time
import warnings
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import TIINGO_API_KEY  # noqa: E402

TIINGO_API_KEY_2 = os.environ.get("TIINGO_API_KEY_2", "")
CACHE = Path(__file__).resolve().parent / "data" / "_bt_cache" / "prices"
COLS = ["open", "high", "low", "close", "volume", "split", "dividend"]


def _cache_path(t: str) -> Path:
    return CACHE / f"{t.upper()}.csv"


def _from_cache(t: str):
    p = _cache_path(t)
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"]).set_index("date")
    return df if not df.empty else None


def _to_cache(t: str, df: pd.DataFrame) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    df.rename_axis("date").reset_index().to_csv(_cache_path(t), index=False)


def _yf(t: str, start: str, end: str):
    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = yf.download(t.replace(".", "-"), start=start, end=end, interval="1d",
                          auto_adjust=False, actions=True, progress=False)
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]
    raw = raw.rename(columns=str.lower)
    if "close" not in raw.columns:
        return None
    out = pd.DataFrame(index=raw.index)
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = raw.get(c)
    spl = raw.get("stock splits")
    out["split"] = (spl.replace(0, 1.0) if spl is not None else 1.0)
    out["dividend"] = raw.get("dividends", 0.0)
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.dropna(subset=["close"])


def _tiingo(t: str, start: str, end: str, key: str):
    import requests
    try:
        r = requests.get(f"https://api.tiingo.com/tiingo/daily/{t}/prices",
                         params={"startDate": start, "endDate": end, "token": key},
                         timeout=30)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    j = r.json()
    if not isinstance(j, list) or not j:
        return None
    df = pd.DataFrame(j)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df = df.set_index("date")
    out = pd.DataFrame(index=df.index)
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = df.get(c)
    out["split"] = df.get("splitFactor", 1.0)
    out["dividend"] = df.get("divCash", 0.0)
    return out.dropna(subset=["close"])


def get(ticker: str, start: str = "2024-06-01", end: str = "2026-06-25",
        allow_tiingo: bool = True):
    """Raw OHLCV + split/dividend for one ticker; None if unavailable anywhere.
    yfinance first; Tiingo (primary then _2) only for what yfinance lacks."""
    cached = _from_cache(ticker)
    if cached is not None:
        return cached
    df = _yf(ticker, start, end)
    src = "yfinance"
    if (df is None or len(df) < 20) and allow_tiingo:
        # _2 (dedicated backtest key) first; primary only as a last resort.
        for key, label in ((TIINGO_API_KEY_2, "tiingo_2"), (TIINGO_API_KEY, "tiingo")):
            if not key:
                continue
            d2 = _tiingo(ticker, start, end, key)
            time.sleep(0.2)
            if d2 is not None and len(d2) >= 20:
                df, src = d2, label
                break
    if df is not None and len(df) >= 20:
        df.attrs["source"] = src
        _to_cache(ticker, df)
        return df
    return None


if __name__ == "__main__":
    # smoke test: a survivor (yfinance) + a delisted name (yfinance->Tiingo fallback)
    for t in (sys.argv[1:] or ["AAPL", "NVDA", "ACCD"]):
        d = get(t)
        if d is None:
            print(f"  {t:8} -> NO DATA (yfinance + Tiingo both failed)")
        else:
            print(f"  {t:8} -> {len(d):4} bars {d.index[0].date()}..{d.index[-1].date()} "
                  f"src={d.attrs.get('source','cache')} last_close={d['close'].iloc[-1]:.2f}")
