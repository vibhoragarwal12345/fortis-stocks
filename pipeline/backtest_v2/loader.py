"""PITPriceLoader — point-in-time OHLCV panel for funnel replay.

PIT safety lives HERE, at the loader boundary (pattern absorbed from
HKUDS/Vibe-Trading): the panel is downloaded once, sanity-checked once, and
every consumer gets a view truncated to `<= as_of`. Nothing downstream can
see the future because nothing downstream ever touches the raw panel.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "backtest_v2_cache"
BENCHMARK = "SPY"


def _to_yahoo(t: str) -> str:
    return t.replace("/", "-").replace(".", "-")


def _sanity_mask(df: pd.DataFrame) -> pd.DataFrame:
    """Drop structurally impossible bars (high < low, non-positive prices).

    Central OHLC integrity guard — every consumer inherits it.
    """
    bad = (
        (df["High"] < df["Low"])
        | (df["Close"] <= 0)
        | (df["Open"] <= 0)
        | df["Close"].isna()
    )
    if bad.any():
        df = df[~bad]
    return df


class PITPriceLoader:
    """Downloads one OHLCV panel covering [start - lookback, end + forward]
    and serves per-ticker histories truncated to an as-of date."""

    def __init__(self, tickers: list[str], start: date, end: date,
                 lookback_days: int = 380, forward_days: int = 45,
                 batch_size: int = 150, cache_key: str | None = None):
        self.tickers = sorted({*tickers, BENCHMARK})
        self.fetch_start = start - pd.Timedelta(days=lookback_days)
        self.fetch_end = end + pd.Timedelta(days=forward_days)
        self.batch_size = batch_size
        self._panel: dict[str, pd.DataFrame] = {}
        self._cache_path = None
        if cache_key:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._cache_path = CACHE_DIR / f"panel_{cache_key}.parquet"

    # ── loading ────────────────────────────────────────────────────────────
    def load(self) -> None:
        if self._cache_path and self._cache_path.exists():
            flat = pd.read_parquet(self._cache_path)
            for t, sub in flat.groupby("ticker"):
                self._panel[t] = sub.drop(columns="ticker").set_index("Date")
            log.info("PIT panel loaded from cache: %d tickers", len(self._panel))
            return

        import yfinance as yf
        kept, failed = 0, 0
        for i in range(0, len(self.tickers), self.batch_size):
            chunk = self.tickers[i: i + self.batch_size]
            yahoo = [_to_yahoo(t) for t in chunk]
            back = dict(zip(yahoo, chunk))
            try:
                df = yf.download(
                    tickers=" ".join(yahoo),
                    start=self.fetch_start.date() if hasattr(self.fetch_start, "date") else self.fetch_start,
                    end=self.fetch_end.date() if hasattr(self.fetch_end, "date") else self.fetch_end,
                    interval="1d", group_by="ticker", auto_adjust=False,
                    progress=False, threads=True,
                )
            except Exception as exc:
                log.warning("panel batch %d failed: %s", i, exc)
                failed += len(chunk)
                continue
            for yt in yahoo:
                try:
                    sub = df[yt] if isinstance(df.columns, pd.MultiIndex) else df
                    sub = sub[["Open", "High", "Low", "Close", "Volume"]].dropna(how="all")
                    sub = _sanity_mask(sub)
                    if len(sub) < 25:          # too thin for layer-1 metrics
                        failed += 1
                        continue
                    self._panel[back[yt]] = sub
                    kept += 1
                except (KeyError, TypeError):
                    failed += 1
            time.sleep(0.3)
            if (i // self.batch_size) % 5 == 4:
                log.info("  panel …%d/%d (%d kept)", i + len(chunk), len(self.tickers), kept)
        log.info("PIT panel: %d tickers kept, %d failed", kept, failed)

        if self._cache_path and self._panel:
            flat = pd.concat(
                [df.reset_index().assign(ticker=t) for t, df in self._panel.items()],
                ignore_index=True,
            )
            flat.to_parquet(self._cache_path, index=False)
            log.info("PIT panel cached -> %s", self._cache_path)

    # ── PIT views ──────────────────────────────────────────────────────────
    def asof(self, ticker: str, as_of: date) -> pd.DataFrame | None:
        """History for `ticker` with every bar dated AFTER `as_of` removed."""
        df = self._panel.get(ticker)
        if df is None:
            return None
        idx = df.index.tz_localize(None) if getattr(df.index, "tz", None) is not None else df.index
        cut = df[idx <= pd.Timestamp(as_of)]
        return cut if len(cut) else None

    def forward_closes(self, ticker: str, entry_after: date, n_days: int) -> tuple[float | None, float | None]:
        """(entry_close, close after n_days trading days) strictly AFTER entry date.

        Entry is the first close after `entry_after` (next trading day), matching
        how a subscriber could actually act on a scan published that day.
        """
        df = self._panel.get(ticker)
        if df is None:
            return None, None
        fwd = df[df.index > pd.Timestamp(entry_after)]
        if fwd.empty:
            return None, None
        entry = float(fwd["Close"].iloc[0])
        if len(fwd) <= n_days:
            return entry, None
        return entry, float(fwd["Close"].iloc[n_days])

    def universe(self) -> list[str]:
        return [t for t in self._panel if t != BENCHMARK]

    def lookahead_self_test(self, as_of: date) -> bool:
        """No asof() view may contain a bar newer than as_of."""
        for t in list(self._panel)[:50]:
            v = self.asof(t, as_of)
            if v is not None and v.index.max() > pd.Timestamp(as_of):
                return False
        return True
