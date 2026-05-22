"""
Market Data Agent  (with full technical analysis)
Fetches ~2 years of daily OHLCV for the ticker universe via yfinance, computes
the base snapshot (price, OHLCV, gap_pct, relative_volume) PLUS eight families
of technical indicators, and bulk-inserts everything into market_snapshots.

Technical-analysis groups (one jsonb column each -- see migration 005):
  price_levels        52w / 200d / 20d highs & lows + distance from them
  moving_averages     SMA 10/20/50/100/200, EMA 9/21/50/200, price-vs-MA,
                      golden-cross / death-cross flags (last 5 days)
  momentum            RSI(14), MACD(12,26,9), Stochastic(14,3,3)
  volatility          Bollinger Bands(20,2) + %B, ATR(14), 30d realized vol
  volume_analysis     30d avg volume, relative volume, OBV & A/D line trend
  patterns            breakout/breakdown, up/down trend, inside/outside, gaps
  support_resistance  last 3 swing highs & lows, nearest support / resistance
  relative_strength   performance vs SPY over 1/5/20/60d + RS percentile

Indicators use the `pandas-ta` library (df.ta accessor).

Relative-strength note: tickers.csv has no sector column, so the RS percentile
is ranked across the *scanned set* (universe-wide), not within GICS sector.

Usage:
    python pipeline/agents/market_data.py            # whole universe
    python pipeline/agents/market_data.py 100        # sample of first 100 tickers
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_ta as ta  # noqa: F401  -- registers the df.ta accessor
import yfinance as yf
from supabase import create_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL, TICKER_UNIVERSE_PATH  # noqa: E402

BATCH_SIZE        = 200       # tickers per yfinance.download() call
HISTORY_PERIOD    = "2y"      # enough for 200-day MA + golden-cross lookback
INSERT_BATCH_SIZE = 500
BENCHMARK         = "SPY"     # relative-strength benchmark
SWING_WINDOW      = 5         # bars each side that define a swing high / low

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Small helpers
# ══════════════════════════════════════════════════════════════════════════════

def _f(val) -> float | None:
    """Float or None for a scalar that might be NaN / None."""
    try:
        v = float(val)
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return None


def _last(series) -> float | None:
    """Last non-NaN value of a Series, rounded, as float."""
    if series is None:
        return None
    s = series.dropna()
    return None if s.empty else round(float(s.iloc[-1]), 4)


def _pct(latest, ref) -> float | None:
    """Percent change of `latest` vs `ref`."""
    latest, ref = _f(latest), _f(ref)
    if latest is None or ref is None or ref == 0:
        return None
    return round((latest - ref) / abs(ref) * 100, 3)


def _col(df: pd.DataFrame, prefix: str) -> str | None:
    """First column of `df` whose name starts with `prefix`."""
    for c in df.columns:
        if c.startswith(prefix):
            return c
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Download
# ══════════════════════════════════════════════════════════════════════════════

def _download_batch(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Download HISTORY_PERIOD of daily OHLCV; return {ticker: lowercased-DataFrame}."""
    try:
        raw = yf.download(tickers, period=HISTORY_PERIOD, interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker")
    except Exception as exc:
        log.warning("yfinance download error: %s", exc)
        return {}
    if raw is None or raw.empty:
        return {}

    result: dict[str, pd.DataFrame] = {}
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in tickers:
        try:
            df = (raw[t] if multi else raw).dropna(how="all")
        except KeyError:
            continue
        if df.empty:
            continue
        df = df.rename(columns=str.lower)            # Open->open ... for df.ta
        if {"open", "high", "low", "close", "volume"}.issubset(df.columns):
            result[t] = df
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Swing-point detection (shared by patterns + support/resistance)
# ══════════════════════════════════════════════════════════════════════════════

def _swings(df: pd.DataFrame, k: int = SWING_WINDOW):
    """Return (swing_highs, swing_lows) as chronological [(idx, value), ...].

    A bar is a swing high if its high is the max of the 2k+1-bar window
    centered on it (and likewise, mirrored, for swing lows).
    """
    highs = df["high"].to_numpy()
    lows  = df["low"].to_numpy()
    n = len(highs)
    sh, sl = [], []
    for i in range(k, n - k):
        if highs[i] == highs[i - k:i + k + 1].max():
            sh.append((i, float(highs[i])))
        if lows[i] == lows[i - k:i + k + 1].min():
            sl.append((i, float(lows[i])))
    return sh, sl


# ══════════════════════════════════════════════════════════════════════════════
# Technical-analysis groups
# ══════════════════════════════════════════════════════════════════════════════

def _price_levels(df: pd.DataFrame, price: float) -> dict:
    high, low = df["high"], df["low"]
    h52, l52 = float(high.tail(252).max()), float(low.tail(252).min())
    return {
        "high_52w":           round(h52, 4),
        "low_52w":            round(l52, 4),
        "pct_from_52w_high":  _pct(price, h52),
        "pct_from_52w_low":   _pct(price, l52),
        "high_200d":          round(float(high.tail(200).max()), 4),
        "low_200d":           round(float(low.tail(200).min()), 4),
        "high_20d":           round(float(high.tail(20).max()), 4),
        "low_20d":            round(float(low.tail(20).min()), 4),
        # within 1% of the 52-week extreme
        "near_52w_high":      _pct(price, h52) is not None and _pct(price, h52) >= -1.0,
        "near_52w_low":       _pct(price, l52) is not None and _pct(price, l52) <= 1.0,
    }


def _moving_averages(df: pd.DataFrame, price: float) -> dict:
    close = df["close"]
    out: dict = {"sma": {}, "ema": {}, "vs_price": {}}

    for p in (10, 20, 50, 100, 200):
        val = _last(df.ta.sma(length=p))
        out["sma"][f"sma_{p}"] = val
        if val is not None:
            out["vs_price"][f"sma_{p}"] = {
                "position": "above" if price >= val else "below",
                "pct":      _pct(price, val),
            }
    for p in (9, 21, 50, 200):
        out["ema"][f"ema_{p}"] = _last(df.ta.ema(length=p))

    # Golden / death cross: 50-SMA crossing 200-SMA within the last 5 days.
    sma50  = df.ta.sma(length=50)
    sma200 = df.ta.sma(length=200)
    golden = death = False
    if sma50 is not None and sma200 is not None:
        diff = (sma50 - sma200).dropna()
        if len(diff) >= 6:
            sign = np.sign(diff.tail(6).to_numpy())
            for a, b in zip(sign[:-1], sign[1:]):
                if a <= 0 < b:
                    golden = True
                if a >= 0 > b:
                    death = True
    out["golden_cross"] = golden
    out["death_cross"]  = death
    return out


def _momentum(df: pd.DataFrame) -> dict:
    rsi = _last(df.ta.rsi(length=14))
    rsi_state = ("n/a" if rsi is None else
                 "oversold" if rsi < 30 else
                 "overbought" if rsi > 70 else "neutral")

    macd_out: dict = {"line": None, "signal": None, "histogram": None,
                      "crossover": "none"}
    macd = df.ta.macd()
    if macd is not None and not macd.empty:
        line_c, sig_c, hist_c = (_col(macd, "MACD_"), _col(macd, "MACDs_"),
                                 _col(macd, "MACDh_"))
        macd_out["line"]      = _last(macd[line_c]) if line_c else None
        macd_out["signal"]    = _last(macd[sig_c]) if sig_c else None
        macd_out["histogram"] = _last(macd[hist_c]) if hist_c else None
        if hist_c:                                    # histogram zero-cross == MACD/signal cross
            h = macd[hist_c].dropna().tail(4).to_numpy()
            for a, b in zip(h[:-1], h[1:]):
                if a <= 0 < b:
                    macd_out["crossover"] = "bullish_crossover"
                elif a >= 0 > b:
                    macd_out["crossover"] = "bearish_crossover"

    k = d = None
    stoch = df.ta.stoch()
    if stoch is not None and not stoch.empty:
        k_c, d_c = _col(stoch, "STOCHk_"), _col(stoch, "STOCHd_")
        k = _last(stoch[k_c]) if k_c else None
        d = _last(stoch[d_c]) if d_c else None
    stoch_state = ("n/a" if k is None else
                   "oversold" if k < 20 else
                   "overbought" if k > 80 else "neutral")

    return {
        "rsi": rsi, "rsi_state": rsi_state,
        "macd": macd_out,
        "stochastic": {"k": k, "d": d, "state": stoch_state},
    }


def _volatility(df: pd.DataFrame, price: float) -> dict:
    bb_out: dict = {"upper": None, "middle": None, "lower": None, "pct_b": None}
    bb = df.ta.bbands(length=20, std=2)
    if bb is not None and not bb.empty:
        for key, pref in (("lower", "BBL"), ("middle", "BBM"),
                          ("upper", "BBU"), ("pct_b", "BBP")):
            c = _col(bb, pref)
            bb_out[key] = _last(bb[c]) if c else None

    atr = _last(df.ta.atr(length=14))
    log_ret = np.log(df["close"] / df["close"].shift(1))
    rv = log_ret.tail(30).std()
    realized_vol = round(float(rv) * np.sqrt(252) * 100, 3) if pd.notna(rv) else None

    return {
        "bollinger": bb_out,
        "atr": atr,
        "atr_pct": _pct(price, price - atr) if atr is not None else None,
        "realized_vol_30d_annualized": realized_vol,
    }


def _trend(series, window: int = 20) -> str:
    """Slope-based trend label for the last `window` values of a series."""
    if series is None:
        return "n/a"
    s = series.dropna()
    if len(s) < window:
        return "n/a"
    seg = s.tail(window).to_numpy()
    slope = np.polyfit(range(window), seg, 1)[0]
    scale = np.std(seg) or 1.0
    norm = slope * window / scale
    return "rising" if norm > 0.5 else "falling" if norm < -0.5 else "flat"


def _volume_analysis(df: pd.DataFrame, volume, avg_vol) -> dict:
    return {
        "avg_volume_30d":  int(avg_vol) if avg_vol else None,
        "relative_volume": round(volume / avg_vol, 4) if volume and avg_vol else None,
        "obv_trend_20d":   _trend(df.ta.obv(), 20),
        "ad_line_trend_20d": _trend(df.ta.ad(), 20),
    }


def _patterns(df: pd.DataFrame, price: float, swings) -> dict:
    today, prev = df.iloc[-1], df.iloc[-2]
    prior20 = df.iloc[-21:-1]                          # 20 bars, excluding today
    h20 = float(prior20["high"].max())
    l20 = float(prior20["low"].min())
    avg_vol = float(df["volume"].tail(30).mean())
    rising_vol = bool(_f(today["volume"]) and avg_vol and today["volume"] > avg_vol)

    sh, sl = swings
    hh = len(sh) >= 2 and sh[-1][1] > sh[-2][1]
    hl = len(sl) >= 2 and sl[-1][1] > sl[-2][1]
    lh = len(sh) >= 2 and sh[-1][1] < sh[-2][1]
    ll = len(sl) >= 2 and sl[-1][1] < sl[-2][1]

    return {
        "breakout":   bool(price > h20 and rising_vol),
        "breakdown":  bool(price < l20 and rising_vol),
        "uptrend_hh_hl":   bool(hh and hl),
        "downtrend_lh_ll": bool(lh and ll),
        "inside_day":  bool(today["high"] < prev["high"] and today["low"] > prev["low"]),
        "outside_day": bool(today["high"] > prev["high"] and today["low"] < prev["low"]),
        "gap_up":   bool(today["open"] > prev["high"]),
        "gap_down": bool(today["open"] < prev["low"]),
    }


def _support_resistance(df: pd.DataFrame, price: float, swings) -> dict:
    sh, sl = swings
    res = [round(v, 4) for _, v in sh]
    sup = [round(v, 4) for _, v in sl]
    nearest_res = min((v for v in res if v > price), default=None)
    nearest_sup = max((v for v in sup if v < price), default=None)
    return {
        "swing_highs_resistance": res[-3:],            # last 3, chronological
        "swing_lows_support":     sup[-3:],
        "nearest_resistance":     nearest_res,
        "nearest_support":        nearest_sup,
    }


def _relative_strength(df: pd.DataFrame, spy_close: pd.Series) -> dict:
    close = df["close"]
    out: dict = {}
    rs_score = None
    for n, label in ((1, "1d"), (5, "5d"), (20, "20d"), (60, "60d")):
        rel = None
        if len(close) > n and spy_close is not None and len(spy_close) > n:
            t_ret   = _pct(close.iloc[-1], close.iloc[-1 - n])
            spy_ret = _pct(spy_close.iloc[-1], spy_close.iloc[-1 - n])
            if t_ret is not None and spy_ret is not None:
                rel = round(t_ret - spy_ret, 3)
        out[f"vs_spy_{label}"] = rel
        if n == 60:
            rs_score = rel
    out["rs_score_60d"] = rs_score      # raw input to the cross-sectional rank
    out["rs_percentile"] = None         # filled in by _assign_rs_rank()
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Snapshot assembly
# ══════════════════════════════════════════════════════════════════════════════

def _compute_snapshot(ticker: str, df: pd.DataFrame, spy_close: pd.Series,
                      now: datetime) -> dict | None:
    """Base snapshot + the eight technical-analysis groups."""
    if len(df) < 2:
        return None
    try:
        latest, prev = df.iloc[-1], df.iloc[-2]
        price      = _f(latest.get("close"))
        open_      = _f(latest.get("open"))
        high       = _f(latest.get("high"))
        low        = _f(latest.get("low"))
        prev_close = _f(prev.get("close"))
        if price is None:
            return None

        vol_raw = latest.get("volume")
        volume  = int(vol_raw) if vol_raw is not None and not pd.isna(vol_raw) else None
        vol_series = df["volume"].iloc[-30:].dropna()
        avg_vol    = float(vol_series.mean()) if not vol_series.empty else None

        gap_pct = None
        if open_ is not None and prev_close:
            gap_pct = round((open_ - prev_close) / prev_close * 100, 4)
        rel_vol = None
        if volume is not None and avg_vol and avg_vol > 0:
            rel_vol = round(volume / avg_vol, 4)

        swings = _swings(df)

        return {
            # ── base snapshot (unchanged columns from migration 001) ──────────
            "ticker":          ticker,
            "snapshot_time":   now.isoformat(),
            "price":           round(price, 4),
            "open":            round(open_, 4) if open_ is not None else None,
            "high":            round(high, 4)  if high  is not None else None,
            "low":             round(low, 4)   if low   is not None else None,
            "volume":          volume,
            "avg_volume_30d":  int(avg_vol) if avg_vol is not None else None,
            "gap_pct":         gap_pct,
            "relative_volume": rel_vol,
            # ── technical-analysis groups (jsonb columns from migration 005) ──
            "price_levels":        _price_levels(df, price),
            "moving_averages":     _moving_averages(df, price),
            "momentum":            _momentum(df),
            "volatility":          _volatility(df, price),
            "volume_analysis":     _volume_analysis(df, volume, avg_vol),
            "patterns":            _patterns(df, price, swings),
            "support_resistance":  _support_resistance(df, price, swings),
            "relative_strength":   _relative_strength(df, spy_close),
        }
    except Exception as exc:
        log.debug("Snapshot compute error [%s]: %s", ticker, exc)
        return None


def _assign_rs_rank(rows: list[dict]) -> None:
    """Fill relative_strength.rs_percentile by ranking rs_score across the set."""
    scored = [(r, r["relative_strength"]["rs_score_60d"]) for r in rows
              if r["relative_strength"]["rs_score_60d"] is not None]
    if not scored:
        return
    scored.sort(key=lambda x: x[1])
    n = len(scored)
    for rank, (row, _) in enumerate(scored, start=1):
        row["relative_strength"]["rs_percentile"] = round(rank / n * 100, 1)


# ══════════════════════════════════════════════════════════════════════════════
# Database insert
# ══════════════════════════════════════════════════════════════════════════════

def _bulk_insert(client, rows: list[dict]) -> int:
    inserted = 0
    for i in range(0, len(rows), INSERT_BATCH_SIZE):
        batch = rows[i : i + INSERT_BATCH_SIZE]
        try:
            resp = client.table("market_snapshots").insert(batch).execute()
            inserted += len(resp.data) if resp.data else 0
        except Exception as exc:
            log.warning("Batch insert failed (rows %d-%d): %s", i, i + len(batch), exc)
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════════

def _report(rows: list[dict]) -> None:
    bar = "=" * 72
    print(f"\n{bar}\nTECHNICAL ANALYSIS -- {len(rows)} tickers\n{bar}")

    # Golden crosses today -------------------------------------------------------
    golden = [r for r in rows if r["moving_averages"].get("golden_cross")]
    print(f"\n[ GOLDEN CROSSES (50-SMA over 200-SMA, last 5 days) -- {len(golden)} ]")
    for r in golden:
        ma = r["moving_averages"]["sma"]
        print(f"  {r['ticker']:<8} price ${r['price']:<10.2f} "
              f"SMA50 {ma.get('sma_50')}  SMA200 {ma.get('sma_200')}")
    if not golden:
        print("  (none today)")

    # Breaking 52-week highs -----------------------------------------------------
    new_highs = sorted((r for r in rows if r["price_levels"].get("near_52w_high")),
                       key=lambda r: r["price_levels"]["pct_from_52w_high"] or -999,
                       reverse=True)
    print(f"\n[ BREAKING OUT OF 52-WEEK HIGHS (within 1%) -- {len(new_highs)} ]")
    for r in new_highs:
        pl = r["price_levels"]
        print(f"  {r['ticker']:<8} price ${r['price']:<10.2f} "
              f"52w high ${pl['high_52w']:<10.2f} ({pl['pct_from_52w_high']:+.2f}%)")
    if not new_highs:
        print("  (none)")

    # RSI > 70 -------------------------------------------------------------------
    overbought = sorted((r for r in rows
                         if (r["momentum"].get("rsi") or 0) > 70),
                        key=lambda r: r["momentum"]["rsi"], reverse=True)
    print(f"\n[ OVERBOUGHT -- RSI(14) > 70 -- {len(overbought)} ]")
    for r in overbought:
        m = r["momentum"]
        print(f"  {r['ticker']:<8} RSI {m['rsi']:<7.1f} "
              f"price ${r['price']:<10.2f} MACD {m['macd'].get('crossover')}")
    if not overbought:
        print("  (none)")

    # Strongest relative strength ------------------------------------------------
    ranked = sorted((r for r in rows
                     if r["relative_strength"].get("rs_score_60d") is not None),
                    key=lambda r: r["relative_strength"]["rs_score_60d"],
                    reverse=True)[:10]
    print("\n[ STRONGEST RELATIVE STRENGTH -- top 10 by 60d performance vs SPY ]")
    if ranked:
        print(f"  {'TICKER':<8}{'vs SPY 60d':>12}{'vs SPY 20d':>12}"
              f"{'vs SPY 5d':>12}{'RS %ILE':>10}")
        for r in ranked:
            rs = r["relative_strength"]
            print(f"  {r['ticker']:<8}{(rs['vs_spy_60d'] or 0):>11.2f}%"
                  f"{(rs['vs_spy_20d'] or 0):>11.2f}%"
                  f"{(rs['vs_spy_5d'] or 0):>11.2f}%"
                  f"{(rs['rs_percentile'] or 0):>9.1f}")
    else:
        print("  (no relative-strength data)")
    print(bar + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(limit: int | None = None) -> None:
    tickers: list[str] = (
        pd.read_csv(TICKER_UNIVERSE_PATH)["ticker"].dropna().astype(str).tolist()
    )
    if limit is not None:
        tickers = tickers[:limit]
    total     = len(tickers)
    n_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    log.info("Loaded %d tickers -- %d batch(es) of up to %d", total, n_batches, BATCH_SIZE)

    # Benchmark for relative strength.
    spy_close = None
    try:
        spy = yf.download(BENCHMARK, period=HISTORY_PERIOD, interval="1d",
                          auto_adjust=True, progress=False)
        if spy is not None and not spy.empty:
            spy_close = spy["Close"].dropna()
            if isinstance(spy_close, pd.DataFrame):
                spy_close = spy_close.iloc[:, 0]
        log.info("Benchmark %s loaded (%d bars)", BENCHMARK,
                 0 if spy_close is None else len(spy_close))
    except Exception as exc:
        log.warning("Benchmark %s download failed -- RS will be empty: %s",
                    BENCHMARK, exc)

    now = datetime.now(timezone.utc)
    all_rows: list[dict] = []
    skipped = 0

    for batch_num, i in enumerate(range(0, total, BATCH_SIZE), start=1):
        batch = tickers[i : i + BATCH_SIZE]
        dfs   = _download_batch(batch)
        for t in batch:
            df = dfs.get(t)
            if df is None:
                skipped += 1
                continue
            row = _compute_snapshot(t, df, spy_close, now)
            if row is None:
                skipped += 1
            else:
                all_rows.append(row)
        log.info("Batch %d/%d -- %d/%d returned data -- %d snapshots so far",
                 batch_num, n_batches, len(dfs), len(batch), len(all_rows))

    if not all_rows:
        log.warning("No rows computed -- exiting.")
        return

    _assign_rs_rank(all_rows)
    log.info("Computed %d snapshots, %d tickers skipped.", len(all_rows), skipped)

    try:
        db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        log.info("Inserting %d rows into market_snapshots...", len(all_rows))
        inserted = _bulk_insert(db, all_rows)
        log.info("Done. %d rows inserted.", inserted)
        if inserted == 0:
            log.warning("No rows persisted -- apply supabase/migrations/"
                        "005_extended_market_snapshots.sql, then re-run.")
    except Exception as exc:
        log.warning("Supabase insert skipped/failed: %s", exc)

    _report(all_rows)


if __name__ == "__main__":
    arg: int | None = None
    if len(sys.argv) > 1:
        try:
            arg = int(sys.argv[1])
        except ValueError:
            log.warning("Invalid limit '%s' -- scanning whole universe", sys.argv[1])
    run(arg)
