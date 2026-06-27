"""
Extension / Room-to-Run signals  -- SHADOW MODULE (NOT wired into the live scan)
================================================================================

WHY THIS EXISTS
  The live ranker (layer2_rank.py) scores pure TRAILING strength -- momentum,
  relative volume, breakout, 52w-proximity, RSI -- so every factor rewards a
  stock that has ALREADY moved up, with no measure of whether there is room
  LEFT to run. On the (dev-era) outcome data, picks peaked ~+12.7% intraperiod
  but held only ~+4.2% at 20d: a real "give-back" once the spike is spent.

  This module measures the missing axis: how STRETCHED / late-in-the-move a name
  is right now. A fresh breakout from a base scores LOW extension (room to run);
  a vertical, far-above-the-average blow-off scores HIGH extension (give-back
  risk). It is deliberately standalone:

    * Nothing in the live pipeline imports it -- selection is unchanged.
    * It takes a plain OHLC history and an `asof` cut, with NO lookahead, so the
      SAME function scores a name live AND reconstructs the signal for any past
      pick (ticker + recommended_date) for the mid-July backtest against
      pick_outcomes -- "did extended picks give back more?".

  Intended next step (only after the backtest validates it on clean,
  post-2026-06-17 data): fold `extension_score` into layer2_rank.py as a
  penalty -- it may only ever SUBTRACT (same asymmetry as the conviction grade),
  never inflate a score. The weights/thresholds below are a first, defensible
  guess to be TUNED by that backtest.

POLARITY
  extension_score : 0..100, HIGHER = more stretched / later in the move (worse).
  room_to_run     : 0..100, HIGHER = fresher / more room (better) == 100 - ext.

CLI
  python pipeline/scan/extension.py --selftest          # synthetic sanity check
  python pipeline/scan/extension.py NVDA 2026-06-17      # one real pick (yfinance)
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd


# ── First-cut weights + thresholds (TO BE TUNED by the mid-July backtest) ──────
# The blended score uses the three least-ambiguous, non-conflicting components.
# days_since_breakout and parabolic_share are kept as RAW metrics only: their
# polarity is ambiguous (a name making new highs daily has days_since_breakout=0
# just like a fresh breakout, and parabolic_share ~1.0 for ANY recent move, big
# or small), so folding them into the score blindly would misrank fresh names.
# The backtest decides whether/how to use them.
WEIGHTS = {"atr": 0.50, "rsi": 0.30, "streak": 0.20}
ATR_STRETCH_FULL = 4.0     # 4+ ATR above the 20-DMA == fully extended (100)
RSI_FLOOR, RSI_FULL = 60.0, 90.0
STREAK_START, STREAK_FULL = 2, 8     # up-day streak: <=2 fresh, 8+ vertical


def _clip(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, v)))


# ── Pure indicator helpers (operate on a chronological close/OHLC slice) ───────

def _sma(close: pd.Series, n: int) -> float | None:
    if len(close) < n:
        return None
    return float(close.iloc[-n:].mean())


def _atr(df: pd.DataFrame, n: int = 14) -> float | None:
    if len(df) < n + 1:
        return None
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean().iloc[-1]
    return float(atr) if pd.notna(atr) and atr > 0 else None


def _rsi(close: pd.Series, n: int = 14) -> float | None:
    if len(close) < n + 1:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean().iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def _days_since_breakout(close: pd.Series, lookback_high: int = 20,
                         search: int = 30) -> int | None:
    """Trading days since the most recent close above its prior 20-day high.
    None if no breakout in the last `search` days (then treated as neutral)."""
    n = len(close)
    if n < lookback_high + 1:
        return None
    last = n - 1
    start = max(lookback_high, n - search)
    for i in range(last, start - 1, -1):
        prior_high = close.iloc[i - lookback_high:i].max()
        if close.iloc[i] > prior_high:
            return last - i
    return None


def _up_streak(close: pd.Series) -> int:
    s = 0
    for i in range(len(close) - 1, 0, -1):
        if close.iloc[i] > close.iloc[i - 1]:
            s += 1
        else:
            break
    return s


def _ret(close: pd.Series, n: int) -> float | None:
    if len(close) < n + 1:
        return None
    old = close.iloc[-(n + 1)]
    if old == 0:
        return None
    return float((close.iloc[-1] / old - 1) * 100)


# ── The signal ────────────────────────────────────────────────────────────────

def compute_extension(df: pd.DataFrame, asof_index: int = -1) -> dict:
    """Extension metrics + score for the row at `asof_index` (default last).

    df: chronological daily OHLC, lowercased columns open/high/low/close.
    asof_index: positional row to evaluate AS OF. Only data up to and including
    that row is used -- no lookahead -- so this is safe for backtesting a pick
    made on a past date. Pass the integer position of recommended_date."""
    if df is None or df.empty or "close" not in df.columns:
        return {"extension_score": None, "room_to_run": None,
                "note": "no price data"}
    sub = df.iloc[: asof_index + 1] if asof_index >= 0 else df
    if len(sub) < 25:
        return {"extension_score": None, "room_to_run": None,
                "note": "insufficient history (<25 rows)"}

    close = sub["close"].astype(float)
    px = float(close.iloc[-1])
    sma20 = _sma(close, 20)
    sma50 = _sma(close, 50)
    atr = _atr(sub, 14)
    rsi = _rsi(close, 14)
    days_bo = _days_since_breakout(close)
    streak = _up_streak(close)
    ret5, ret20 = _ret(close, 5), _ret(close, 20)

    pct_above_sma20 = (px / sma20 - 1) * 100 if sma20 else None
    pct_above_sma50 = (px / sma50 - 1) * 100 if sma50 else None
    atr_stretch = ((px - sma20) / atr) if (sma20 and atr) else None  # in ATR units
    # share of the 20-day gain made in the last 5 days (accel / verticality)
    parabolic = (ret5 / ret20) if (ret5 is not None and ret20 and ret20 > 0) else None

    # ── sub-scores: 0..100, higher = more extended ──
    s_atr = _clip(atr_stretch / ATR_STRETCH_FULL * 100) if atr_stretch is not None else None
    s_rsi = _clip((rsi - RSI_FLOOR) / (RSI_FULL - RSI_FLOOR) * 100) if rsi is not None else None
    s_streak = _clip((streak - STREAK_START) / (STREAK_FULL - STREAK_START) * 100)

    parts = [(WEIGHTS["atr"], s_atr), (WEIGHTS["rsi"], s_rsi),
             (WEIGHTS["streak"], s_streak)]
    parts = [(w, sc) for w, sc in parts if sc is not None]
    if parts:
        wsum = sum(w for w, _ in parts)
        extension = round(sum(w * sc for w, sc in parts) / wsum, 1)
        room = round(100 - extension, 1)
    else:
        extension = room = None

    return {
        "asof": str(sub.index[-1])[:10] if hasattr(sub.index[-1], "__str__") else None,
        "price": round(px, 4),
        "extension_score": extension,
        "room_to_run": room,
        # raw metrics (each individually backtestable against the give-back)
        "metrics": {
            "pct_above_sma20": round(pct_above_sma20, 2) if pct_above_sma20 is not None else None,
            "pct_above_sma50": round(pct_above_sma50, 2) if pct_above_sma50 is not None else None,
            "atr_stretch_units": round(atr_stretch, 2) if atr_stretch is not None else None,
            "rsi_14": round(rsi, 1) if rsi is not None else None,
            "days_since_breakout": days_bo,
            "up_day_streak": streak,
            "ret_5d_pct": round(ret5, 2) if ret5 is not None else None,
            "ret_20d_pct": round(ret20, 2) if ret20 is not None else None,
            "parabolic_share_5of20": round(parabolic, 2) if parabolic is not None else None,
        },
        "subscores": {"atr": s_atr, "rsi": s_rsi, "streak": s_streak},
    }


def _as_date(d) -> date:
    return (d if isinstance(d, date) and not isinstance(d, datetime)
            else datetime.fromisoformat(str(d)[:10]).date())


def _download(ticker: str, start: date, end_inclusive: date):
    """Daily OHLC from yfinance in [start, end_inclusive], lowercased columns.
    None if unavailable. (Local import: only these shadow paths need yfinance.)"""
    import yfinance as yf
    try:
        raw = yf.download(ticker, start=start.isoformat(),
                          end=(end_inclusive + timedelta(days=1)).isoformat(),
                          interval="1d", auto_adjust=True, progress=False)
    except Exception:
        return None
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]
    df = raw.rename(columns=str.lower)
    keep = [c for c in ("open", "high", "low", "close") if c in df.columns]
    return df[keep].dropna()


def extension_for_pick(ticker: str, asof_date) -> dict | None:
    """Pull ~5 months of history up to asof_date and compute extension AS OF
    that date (no lookahead). None if price history is unavailable."""
    d = _as_date(asof_date)
    df = _download(ticker, d - timedelta(days=170), d)
    if df is None or df.empty:
        return None
    out = compute_extension(df, asof_index=-1)
    out["ticker"] = ticker
    return out


# ── Advisory layer: freshness tag + reference entry zone + hold window ─────────
# These operationalize the timing debate: instead of "buy at the gapped open",
# tag HOW we caught the move and suggest a disciplined REFERENCE entry + horizon.
# Everything is deliberately "reference" framed (research, not advice). The SETUP
# detector here is a coarse proxy (near-resistance + low extension); the full
# volatility-squeeze / base detector is the next build.

BREAKOUT_GAP_CHASE = 4.0   # a same-bar breakout that gaps more than this == a chase


def _gap_pct(sub: pd.DataFrame) -> float | None:
    if len(sub) < 2 or "open" not in sub.columns:
        return None
    prev_close = float(sub["close"].iloc[-2])
    today_open = float(sub["open"].iloc[-1])
    return round((today_open / prev_close - 1) * 100, 2) if prev_close > 0 else None


def _pivot(close: pd.Series, lookback: int = 20) -> float | None:
    if len(close) < lookback + 1:
        return None
    return float(close.iloc[-(lookback + 1):-1].max())


def classify_breakout(ext: dict, gap_pct: float | None) -> str:
    """Where in the move did we catch it? Drives the entry/hold logic.
    EXTENDED / GAP_CHASE / STALE = the timing traps; FRESH_PIVOT / SETUP = good."""
    if not ext or ext.get("extension_score") is None:
        return "UNKNOWN"
    e = ext["extension_score"]
    m = ext.get("metrics", {})
    days = m.get("days_since_breakout")
    above20 = m.get("pct_above_sma20")
    if e >= 65:
        return "EXTENDED"        # already had its run -- the give-back risk
    if days is None:
        # no breakout in the window: coiling just under resistance == a setup
        return ("SETUP" if (above20 is not None and -2.0 <= above20 <= 6.0 and e < 45)
                else "NEUTRAL")
    if days <= 1 and gap_pct is not None and gap_pct > BREAKOUT_GAP_CHASE:
        return "GAP_CHASE"       # broke out but gapped hard -> the exact timing trap
    if days <= 1 and e < 50:
        return "FRESH_PIVOT"     # just broke out, not extended -- the sweet spot
    if days >= 4:
        return "STALE"           # broke out days ago, momentum aging
    return "MID_MOVE"


def suggested_entry(sub: pd.DataFrame, ext: dict, tag: str) -> dict:
    """A REFERENCE buy zone (not advice): near the pivot for fresh/setup names,
    a pullback toward the 20-day average for extended/chased ones."""
    close = sub["close"].astype(float)
    px = float(close.iloc[-1])
    atr = _atr(sub, 14) or px * 0.02
    sma20 = _sma(close, 20) or px
    pivot = _pivot(close, 20) or px
    support = (float(sub["low"].iloc[-20:].min())
               if "low" in sub.columns and len(sub) >= 20 else sma20)
    if tag in ("EXTENDED", "GAP_CHASE", "STALE"):
        return {"key_level": round(sma20, 2),
                "buy_zone_low": round(min(support, sma20), 2),
                "buy_zone_high": round(sma20, 2),
                "do_not_chase_above": round(px, 2),
                "basis": "extended/chased -- reference entry is a pullback toward the "
                         "20-day average, NOT the current price"}
    if tag == "SETUP":
        return {"key_level": round(pivot, 2),
                "buy_zone_low": round(px, 2),
                "buy_zone_high": round(pivot + 0.3 * atr, 2),
                "do_not_chase_above": round(pivot + 1.0 * atr, 2),
                "basis": "coiling under resistance -- reference entry on a break above "
                         "the pivot; do not chase beyond ~1 ATR"}
    if tag in ("FRESH_PIVOT", "MID_MOVE"):
        return {"key_level": round(pivot, 2),
                "buy_zone_low": round(pivot, 2),
                "buy_zone_high": round(pivot + 0.5 * atr, 2),
                "do_not_chase_above": round(pivot + 1.2 * atr, 2),
                "basis": "just above the breakout level -- reference buy zone near the "
                         "pivot; do not chase beyond ~1 ATR"}
    return {"key_level": round(sma20, 2),
            "buy_zone_low": round(support, 2), "buy_zone_high": round(sma20, 2),
            "do_not_chase_above": None,
            "basis": "reference entry zone around support / the 20-day average"}


def estimated_hold(tag: str) -> dict:
    """A REFERENCE holding horizon, anchored to the ~1-month statistical price
    window and nudged by the tag. Not a deadline -- exit on thesis/stop."""
    lo, hi = 10, 25
    if tag == "EXTENDED":
        lo, hi = 5, 15
    elif tag == "SETUP":
        lo, hi = 15, 30
    return {"low_trading_days": lo, "high_trading_days": hi,
            "approx_weeks": f"{round(lo / 5)}-{round(hi / 5)}",
            "basis": "reference horizon tied to the ~1-month statistical price window; "
                     "not a deadline -- exit on thesis or stop, not the clock"}


def evaluate_pick(ticker: str, asof_date) -> dict | None:
    """Full shadow advisory for a pick made on asof_date: extension + freshness
    tag + reference entry zone + estimated hold + the NEXT-OPEN price (what a
    pre-market-pick buyer actually pays) and the timing cost vs the close we
    currently track. As-of signals are no-lookahead; next_open is the only
    post-asof field and is labelled as such."""
    d = _as_date(asof_date)
    df = _download(ticker, d - timedelta(days=170), d + timedelta(days=12))
    if df is None or df.empty:
        return None
    idx_dates = [pd.Timestamp(x).date() for x in df.index]
    asof_pos = max((i for i, dt in enumerate(idx_dates) if dt <= d), default=None)
    if asof_pos is None or asof_pos < 24:
        return {"ticker": ticker, "note": "insufficient history before asof"}
    sub = df.iloc[: asof_pos + 1]
    ext = compute_extension(df, asof_index=asof_pos)
    gap = _gap_pct(sub)
    tag = classify_breakout(ext, gap)
    close_entry = float(sub["close"].iloc[-1])
    nxt = df.iloc[asof_pos + 1:]
    next_open = float(nxt["open"].iloc[0]) if len(nxt) and "open" in nxt.columns else None
    return {
        "ticker": ticker,
        "asof": str(sub.index[-1])[:10],
        "tag": tag,
        "extension_score": ext.get("extension_score"),
        "room_to_run": ext.get("room_to_run"),
        "gap_pct": gap,
        "close_on_asof": round(close_entry, 4),
        "next_open": round(next_open, 4) if next_open else None,
        "next_open_date": str(nxt.index[0])[:10] if len(nxt) else None,
        "timing_cost_open_vs_close_pct": (round((next_open / close_entry - 1) * 100, 2)
                                          if next_open and close_entry else None),
        "suggested_entry": suggested_entry(sub, ext, tag),
        "estimated_hold": estimated_hold(tag),
        "metrics": ext.get("metrics"),
    }


# ── Self-test: a fresh breakout must score LESS extended than a blow-off ───────

def _synthetic(kind: str) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    if kind == "early":
        # long quiet base ~100, then a modest, controlled break to ~104 over 5d.
        base = 100 + rng.normal(0, 0.6, 50)
        climb = np.linspace(float(base[-1]), 104.0, 6)[1:]
        closes = np.append(base, climb)
    elif kind == "extended":
        # quiet base, then 8 sessions accelerating vertically to ~145 (+~45%).
        base = 100 + rng.normal(0, 0.6, 48)
        blow = np.linspace(float(base[-1]), 145.0, 9)[1:]
        closes = np.append(base, blow)
    else:
        raise ValueError(kind)
    closes = np.asarray(closes, float)
    idx = pd.bdate_range(end="2026-06-17", periods=len(closes))
    high = closes * 1.012
    low = closes * 0.988
    openp = np.append(closes[0], closes[:-1])
    return pd.DataFrame({"open": openp, "high": high, "low": low, "close": closes}, index=idx)


def _advise(df: pd.DataFrame) -> dict:
    ext = compute_extension(df)
    gap = _gap_pct(df)
    tag = classify_breakout(ext, gap)
    return {"ext": ext["extension_score"], "tag": tag,
            "entry": suggested_entry(df, ext, tag), "hold": estimated_hold(tag)}


def _selftest() -> int:
    early = _advise(_synthetic("early"))
    ext = _advise(_synthetic("extended"))
    print(f"EARLY (modest breakout): ext={early['ext']} tag={early['tag']} "
          f"hold={early['hold']['approx_weeks']}w entry={early['entry']['basis']}")
    print(f"EXTENDED (blow-off run): ext={ext['ext']} tag={ext['tag']} "
          f"hold={ext['hold']['approx_weeks']}w entry={ext['entry']['basis']}")
    ok = True
    if not (ext["ext"] > early["ext"]):
        print("  FAIL: a blow-off should score MORE extended than a modest breakout"); ok = False
    if ext["tag"] != "EXTENDED":
        print(f"  FAIL: blow-off should tag EXTENDED, got {ext['tag']}"); ok = False
    if early["tag"] == "EXTENDED":
        print(f"  FAIL: modest breakout should not tag EXTENDED, got {early['tag']}"); ok = False
    if not (ext["hold"]["high_trading_days"] < early["hold"]["high_trading_days"]):
        print("  FAIL: extended hold horizon should be shorter than a fresh setup"); ok = False
    if "pullback" not in ext["entry"]["basis"]:
        print("  FAIL: extended entry should advise a pullback, not the current price"); ok = False
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    args = sys.argv[1:]
    if not args or "--selftest" in args:
        return _selftest()
    if len(args) >= 2:
        import json
        res = evaluate_pick(args[0].upper(), args[1])
        print(json.dumps(res, indent=2, default=str))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
