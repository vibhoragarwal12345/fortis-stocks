"""
Layer 1 -- Fast Scan
====================

Runs over EVERY ticker in the liquid US universe (~3-5k names). Pure math
only, no LLM. For each name it computes price action + volume + RSI + 52w
proximity + breakout flags, then writes one row per ticker to scan_results
under the given scan_id.

ARCHITECTURE
  - Reads pipeline/data/full_universe.csv (built by build_full_universe.py).
  - Batches yfinance downloads (1y period, daily interval) so we get
    enough history for RSI(14) + 20d/52w stats in a single pull per batch.
  - Resilient fetch: Yahoo flakes; we log failures and keep going.
  - Writes scan_results with composite_score=NULL (Layer 2 fills that in).

PERFORMANCE
  ~3,300 tickers / batch_size 100 = ~33 batches × ~5s each = ~3 min.
  Stays well inside the 30-minute target for the full universe.

CLI
  python -m pipeline.scan.layer1_fast_scan --scan-id 123
  python -m pipeline.scan.layer1_fast_scan --scan-id 123 --sample 500
  python -m pipeline.scan.layer1_fast_scan --scan-id 123 --batch 100
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

UNIVERSE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "full_universe.csv"
)


# ──────────────────────────────────────────────────────────────────────────
#  Pure-math metrics
# ──────────────────────────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> float | None:
    """Classic Wilder RSI. Returns the latest value or None if not enough data."""
    if len(close) < period + 1:
        return None
    delta = close.diff().dropna()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    v = rsi.iloc[-1]
    return None if pd.isna(v) else float(v)


def _per_ticker_metrics(close: pd.Series, volume: pd.Series, open_px: pd.Series) -> dict:
    """Compute the Layer-1 cheap metrics from a single ticker's 1y OHLCV.

    All metrics are robust to short histories (recent IPOs etc.) -- they
    return None when there isn't enough data instead of NaN-ing the whole
    row.
    """
    out: dict = {}
    close = close.dropna()
    if close.empty:
        return out

    out["price"] = float(close.iloc[-1])
    out["data_as_of"] = close.index[-1].to_pydatetime().replace(tzinfo=timezone.utc).isoformat()

    # Day change vs previous close
    if len(close) >= 2:
        prev = float(close.iloc[-2])
        if prev > 0:
            out["day_change_pct"] = round((out["price"] / prev - 1) * 100, 3)

        # Gap = today's open vs yesterday's close
        if not open_px.empty:
            today_open = float(open_px.iloc[-1])
            if today_open > 0:
                out["gap_pct"] = round((today_open / prev - 1) * 100, 3)

    # 5-day return
    if len(close) >= 6:
        out["return_5d_pct"] = round(
            (float(close.iloc[-1]) / float(close.iloc[-6]) - 1) * 100, 3
        )
    # 20-day return (momentum)
    if len(close) >= 21:
        out["return_20d_pct"] = round(
            (float(close.iloc[-1]) / float(close.iloc[-21]) - 1) * 100, 3
        )

    # Relative volume vs 20-day average
    vol = volume.dropna()
    if len(vol) >= 21:
        avg20 = float(vol.iloc[-21:-1].mean())
        if avg20 > 0:
            out["relative_volume"] = round(float(vol.iloc[-1]) / avg20, 3)
    if len(vol):
        # Raw latest volume for the Benford feed-integrity tripwire.
        # Underscore prefix: in-memory only, persist() never writes it.
        out["_latest_volume"] = float(vol.iloc[-1])

    # Alpha-bench factors (added 2026-07-15; absorbed from the Vibe-Trading
    # zoo bench — see pipeline/backtest_v2/factor_bench.py). All three were
    # HEALTHY with the same sign in both test windows:
    #   volume_vol_20d  std of daily volume %-change, 20d  (IC −: churn bad)
    #   low20_ratio     min(close,20d)/close               (IC +: near-lows good)
    #   pv_corr_20d     corr(close, log1p(volume)), 20d    (IC −: rally-on-volume bad)
    if len(vol) >= 21 and len(close) >= 21:
        vchg = vol.iloc[-21:].pct_change().dropna()
        if len(vchg) >= 10 and float(vchg.std()) == float(vchg.std()):  # not NaN
            out["volume_vol_20d"] = round(float(vchg.std()), 4)
        out["low20_ratio"] = round(float(close.iloc[-20:].min()) / out["price"], 4) \
            if out["price"] > 0 else None
        c20 = close.iloc[-20:]
        v20 = np.log1p(vol.iloc[-20:].astype(float))
        if c20.std() > 0 and v20.std() > 0:
            pv = float(c20.corr(v20))
            if pv == pv:  # not NaN
                out["pv_corr_20d"] = round(pv, 4)

    # RSI(14)
    rsi = _rsi(close, 14)
    if rsi is not None:
        out["rsi_14"] = round(rsi, 2)

    # 52-week stats. Need ~252 trading days for the full window; if we
    # have less, we use whatever we have but mark it implicitly via the
    # length-conditional shortfall.
    if len(close) >= 60:
        window = close.iloc[-min(252, len(close)):]
        hi = float(window.max())
        lo = float(window.min())
        if hi > 0:
            out["pct_from_52w_high"] = round(
                (out["price"] / hi - 1) * 100, 3
            )
        if lo > 0:
            out["pct_from_52w_low"] = round(
                (out["price"] / lo - 1) * 100, 3
            )
        # Trailing 52-week return (vs the start of the window) -- the one
        # simple number the dashboard displays.
        base = float(window.iloc[0])
        if base > 0:
            out["return_52w_pct"] = round(
                (out["price"] / base - 1) * 100, 3
            )

        # Breakout = closed above the prior 20d high (excluding today)
        if len(close) >= 21:
            prior_20_high = float(close.iloc[-21:-1].max())
            prior_20_low = float(close.iloc[-21:-1].min())
            out["is_breakout"] = bool(out["price"] > prior_20_high)
            out["is_breakdown"] = bool(out["price"] < prior_20_low)

    # Sanity-clean NaN floats so the JSON serializer in supabase-py
    # doesn't reject them.
    for k, v in list(out.items()):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            out[k] = None
    return out


# ──────────────────────────────────────────────────────────────────────────
#  Fetch loop
# ──────────────────────────────────────────────────────────────────────────

def _to_yahoo(t: str) -> str:
    return t.replace("/", "-").replace(".", "-")


# ── Sina fallback (added 2026-07-15) ──────────────────────────────────────
# Absorbed from HKUDS/Vibe-Trading's ban-risk-ordered loader chain: when the
# primary (yfinance) drops a ticker — Yahoo throttling, symbol quirks — we
# retry it against Sina Finance's free, no-auth US daily-K JSONP endpoint
# instead of accepting a hole in the universe. (Stooq, their other US
# fallback, now sits behind a JS browser check — verified 2026-07-15.)
# Capped + throttled so a bad day at Yahoo can't blow the Layer-1 budget.

FALLBACK_MAX = 150          # bound the extra runtime (~0.5s each)
FALLBACK_THROTTLE_SEC = 0.5
_SINA_URL = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/"
             "var%20x=/US_MinKService.getDailyK?symbol={sym}")


def _fetch_sina_one(ticker: str) -> pd.DataFrame | None:
    """~1y of daily OHLCV from Sina's US daily-K JSONP, or None.
    Response: `var x=([{"d":"YYYY-MM-DD","o":..,"h":..,"l":..,"c":..,"v":..},…])`."""
    import json as _json
    import urllib.request
    sym = ticker.lower().replace("/", ".")   # BRK/B style → brk.b
    req = urllib.request.Request(
        _SINA_URL.format(sym=sym),
        headers={"User-Agent": "Mozilla/5.0",
                 "Referer": "https://stock.finance.sina.com.cn/"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("utf-8", errors="replace")
        start, end = raw.find("(["), raw.rfind("])")
        if start < 0 or end < 0:
            return None
        bars = _json.loads(raw[start + 1: end + 1])
        if len(bars) < 25:
            return None
        df = pd.DataFrame(bars).rename(columns={
            "d": "Date", "o": "Open", "h": "High", "l": "Low",
            "c": "Close", "v": "Volume"})
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index().tail(260)
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["Close"])
    except Exception:
        return None


def _sina_fallback(failed: list[str]) -> list[dict]:
    """Recover Layer-1 metrics for yfinance-failed tickers via Sina."""
    out: list[dict] = []
    batch = failed[:FALLBACK_MAX]
    if not batch:
        return out
    log.info("Sina fallback: retrying %d yfinance-failed tickers…", len(batch))
    for t in batch:
        df = _fetch_sina_one(t)
        time.sleep(FALLBACK_THROTTLE_SEC)
        if df is None or len(df) < 25:
            continue
        try:
            metrics = _per_ticker_metrics(df["Close"], df["Volume"], df["Open"])
        except (KeyError, TypeError):
            continue
        if metrics:
            metrics["ticker"] = t
            out.append(metrics)
    log.info("Sina fallback recovered %d/%d tickers", len(out), len(batch))
    return out


def fetch_and_score(
    tickers: list[str],
    batch_size: int = 100,
    period: str = "1y",
) -> list[dict]:
    try:
        import yfinance as yf
    except ImportError:
        raise SystemExit("yfinance not installed; pip install yfinance")

    log.info(
        "Layer 1: fetching %dy OHLCV for %d tickers (batch=%d)…",
        1 if period == "1y" else period,
        len(tickers),
        batch_size,
    )
    out: list[dict] = []
    t0 = time.time()
    failures = 0
    failed_tickers: list[str] = []
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
            log.warning("  batch %d-%d failed entirely: %s", i, i + len(chunk), exc)
            failures += len(chunk)
            failed_tickers.extend(chunk)
            continue

        for yt in yahoo_chunk:
            orig = yahoo_to_orig[yt]
            try:
                sub = df[yt] if isinstance(df.columns, pd.MultiIndex) else df
                close = sub["Close"]
                volume = sub["Volume"]
                open_px = sub["Open"]
                metrics = _per_ticker_metrics(close, volume, open_px)
                if not metrics:
                    failures += 1
                    failed_tickers.append(orig)
                    continue
                metrics["ticker"] = orig
                out.append(metrics)
            except (KeyError, TypeError):
                failures += 1
                failed_tickers.append(orig)

        time.sleep(0.3)
        if (i // batch_size + 1) % 5 == 0:
            done = min(i + batch_size, len(tickers))
            elapsed = time.time() - t0
            eta = (len(tickers) - done) / max(done, 1) * elapsed
            log.info(
                "  …%d/%d  (%d kept, %d failed, eta %.0fs)",
                done,
                len(tickers),
                len(out),
                failures,
                eta,
            )
    # Second chance for anything Yahoo dropped.
    if failed_tickers:
        recovered = _sina_fallback(failed_tickers)
        out.extend(recovered)
        failures -= len(recovered)

    log.info(
        "Layer 1 fetch complete: %d kept, %d failed (%.1f%% kept)",
        len(out),
        failures,
        100 * len(out) / max(len(tickers), 1),
    )

    # Feed-integrity tripwire (2026-07-15, financial_rigor): Benford's-law
    # first-digit check over the universe's latest daily volumes. Volumes
    # span orders of magnitude, so a healthy feed conforms; NONCONFORMITY
    # means the price/volume feed itself is suspect (synthetic, truncated,
    # or corrupted data). Log-only — a tripwire, not a gate.
    try:
        from agents.financial_rigor import benford
        raw_vols = [r["_latest_volume"] for r in out if r.get("_latest_volume")]
        if raw_vols:
            b = benford(raw_vols)
            if b.get("verdict") == "NONCONFORMITY":
                log.warning("BENFORD TRIPWIRE: universe volume distribution "
                            "nonconforming (MAD=%s, n=%s) — inspect the feed",
                            b.get("mad"), b.get("n"))
            else:
                log.info("Benford feed check: %s (MAD=%s, n=%s)",
                         b.get("verdict"), b.get("mad"), b.get("n"))
    except Exception as exc:   # noqa: BLE001 -- tripwire must never break the scan
        log.debug("benford tripwire skipped: %s", exc)
    return out


# ──────────────────────────────────────────────────────────────────────────
#  Persist
# ──────────────────────────────────────────────────────────────────────────

def persist(rows: list[dict], scan_id: int) -> int:
    if not rows:
        return 0
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    payload = [
        {
            "scan_id":           scan_id,
            "ticker":            r["ticker"],
            "price":             r.get("price"),
            "day_change_pct":    r.get("day_change_pct"),
            "gap_pct":           r.get("gap_pct"),
            "return_5d_pct":     r.get("return_5d_pct"),
            "return_20d_pct":    r.get("return_20d_pct"),
            "relative_volume":   r.get("relative_volume"),
            "rsi_14":            r.get("rsi_14"),
            "pct_from_52w_high": r.get("pct_from_52w_high"),
            "pct_from_52w_low":  r.get("pct_from_52w_low"),
            "return_52w_pct":    r.get("return_52w_pct"),
            "is_breakout":       r.get("is_breakout"),
            "is_breakdown":      r.get("is_breakdown"),
            "volume_vol_20d":    r.get("volume_vol_20d"),
            "low20_ratio":       r.get("low20_ratio"),
            "pv_corr_20d":       r.get("pv_corr_20d"),
            "data_as_of":        r.get("data_as_of"),
        }
        for r in rows
    ]
    n = 0
    # Chunked upsert; Supabase REST gets unhappy past ~500 rows per call.
    for i in range(0, len(payload), 500):
        chunk = payload[i : i + 500]
        db.table("scan_results").upsert(chunk, on_conflict="scan_id,ticker").execute()
        n += len(chunk)
    return n


# ──────────────────────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────────────────────

def load_universe(sample: int | None = None) -> list[str]:
    if not UNIVERSE_PATH.exists():
        raise SystemExit(
            f"Universe missing: {UNIVERSE_PATH}. "
            "Run `python pipeline/data/build_full_universe.py` first."
        )
    df = pd.read_csv(UNIVERSE_PATH)
    tickers = df["ticker"].astype(str).str.upper().tolist()
    if sample:
        tickers = tickers[:sample]
    return tickers


def run(scan_id: int, sample: int | None = None, batch_size: int = 100) -> int:
    tickers = load_universe(sample=sample)
    rows = fetch_and_score(tickers, batch_size=batch_size)
    n = persist(rows, scan_id=scan_id)
    log.info("Wrote %d scan_results rows for scan_id=%d", n, scan_id)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-id", type=int, required=True,
                    help="market_scans.id row to attach results to.")
    ap.add_argument("--sample", type=int, default=None)
    ap.add_argument("--batch",  type=int, default=100)
    args = ap.parse_args()
    run(scan_id=args.scan_id, sample=args.sample, batch_size=args.batch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
