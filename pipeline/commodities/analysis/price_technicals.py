"""
Agent 1: price technicals — the one layer that transfers directly from the
stock engine. Trend vs 50/200 DMA, RSI(14), ATR(14), realized vol, 52w range
position, swing support/resistance, and Monte Carlo reference bands.

Monte Carlo reuses quant/monte_carlo.MonteCarloSimulator (generic math, not
company-centric): the distribution is FITTED to log returns (Normal vs
Student-t by Jarque-Bera) for the volatility/tail shape, but the fitted
location is then ZEROED before simulating. Carrying the empirical mean
return forward extrapolates the trailing trend — over 252 days that turned
a 2-year gold rally into a "+69% median" band, i.e. a momentum forecast
dressed as statistics. With zero drift the bands are a pure volatility
cone around spot: where price could plausibly sit given its return
distribution, with NO directional assumption. Direction belongs to the
trend/momentum layer, not the bands.
Bands are computed for BOTH horizons separately:
  tactical    5 trading days   (~1 week)   — for the SHORT-TERM read
                                             (matches the weekly scan cadence)
  structural  252 trading days (~1 year)   — for the STRUCTURAL read
and are labeled STATISTICAL REFERENCE LEVELS, NOT FORECASTS, everywhere.

Iron ore (no daily future) gets no daily technicals — flagged, with only a
low-frequency trend from the monthly FRED series.
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.registry import COMMODITIES  # noqa: E402
from commodities.sources.common import ESTIMATED, UNVERIFIED, utcnow_iso, verified  # noqa: E402
from quant.monte_carlo import MonteCarloSimulator  # noqa: E402

log = logging.getLogger("commodities.technicals")

HISTORY_PERIOD = "2y"
HORIZONS = {"tactical_1w": 5, "structural_252d": 252}
BAND_LABEL = ("STATISTICAL REFERENCE LEVELS — zero-drift volatility cone from a "
              "return-distribution simulation (no directional assumption) — "
              "NOT price forecasts, NOT targets")
SWING_WINDOW = 5


def _f(v, nd: int = 4):
    try:
        v = float(v)
        return None if pd.isna(v) else round(v, nd)
    except (TypeError, ValueError):
        return None


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return _f(100 - 100 / (1 + rs.iloc[-1]), 2)


def _atr(hist: pd.DataFrame, period: int = 14) -> float | None:
    hl = hist["High"] - hist["Low"]
    hc = (hist["High"] - hist["Close"].shift()).abs()
    lc = (hist["Low"] - hist["Close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return _f(tr.ewm(alpha=1 / period, min_periods=period).mean().iloc[-1])


def _swings(hist: pd.DataFrame, n: int = 3) -> dict:
    """Last n swing highs/lows (local extremes over ±SWING_WINDOW bars) and
    the nearest support/resistance relative to the last close."""
    highs, lows = [], []
    h, l = hist["High"].to_numpy(), hist["Low"].to_numpy()
    for i in range(SWING_WINDOW, len(hist) - SWING_WINDOW):
        if h[i] == h[i - SWING_WINDOW:i + SWING_WINDOW + 1].max():
            highs.append(_f(h[i]))
        if l[i] == l[i - SWING_WINDOW:i + SWING_WINDOW + 1].min():
            lows.append(_f(l[i]))
    close = float(hist["Close"].iloc[-1])
    supports = [x for x in lows if x and x < close]
    resistances = [x for x in highs if x and x > close]
    return {
        "swing_highs": highs[-n:], "swing_lows": lows[-n:],
        "nearest_support": max(supports) if supports else None,
        "nearest_resistance": min(resistances) if resistances else None,
    }


def _mc_bands(close_series: pd.Series, current: float) -> dict:
    sim = MonteCarloSimulator()
    cal = sim.calibrate(close_series)
    # Zero the fitted location: keep the volatility and tail shape, drop the
    # historical drift, so the bands carry no directional view (see module
    # docstring for why the drifted version was misleading).
    if sim.distribution_type == "normal":
        sim.params["mu"] = 0.0
    else:
        sim.params["loc"] = 0.0
    out = {
        "label": BAND_LABEL,
        "calibration": {
            "distribution": cal["distribution_type"],
            "quality": cal["calibration_quality"],
            "n_obs": cal["n_obs"],
            "drift": "zeroed — volatility cone only, no directional assumption",
            "verification": ESTIMATED + " (distribution fitted to verified yfinance history)",
        },
    }
    for name, days in HORIZONS.items():
        paths = sim.simulate(current, days)
        s = sim.summary(paths)
        out[name] = {
            "bear_p5": s["p5_price"], "normal_p50": s["median_price"],
            "bull_p95": s["p95_price"],
            "label": BAND_LABEL,
            "verification": ESTIMATED + " (10k simulated zero-drift paths from the fitted distribution)",
        }
    return out


def analyze_one(key: str) -> dict:
    spec = COMMODITIES[key]
    out: dict = {"commodity": key, "name": spec["name"], "computed_at": utcnow_iso()}
    sym = spec["yf_symbol"]
    if sym is None:
        out["status"] = "NO_DAILY_DATA"
        out["verification"] = UNVERIFIED
        out["note"] = ("No free daily future — daily technicals not computable. "
                       "Only the monthly FRED trend (metals_supply layer) applies; "
                       "any technical claim about this commodity would be fabricated.")
        return out

    try:
        hist = yf.Ticker(sym).history(period=HISTORY_PERIOD, auto_adjust=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance %s failed: %s", sym, exc)
        hist = pd.DataFrame()
    if hist.empty or len(hist) < 60:
        out["status"] = "FETCH_FAILED"
        out["verification"] = UNVERIFIED
        return out

    close = hist["Close"]
    current = float(close.iloc[-1])
    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
    hi52 = float(hist["High"].tail(252).max())
    lo52 = float(hist["Low"].tail(252).min())
    rets = np.log(close / close.shift()).dropna()

    out.update({
        "status": "OK",
        "source_note": f"all figures computed from yfinance {sym} {HISTORY_PERIOD} daily history fetched this run",
        "price": {"close": _f(current), "asof": str(hist.index[-1].date()),
                  "verification": verified(f"yfinance {sym}")},
        "trend": {
            "sma50": _f(sma50), "sma200": _f(sma200),
            "above_sma50": bool(current > sma50) if pd.notna(sma50) else None,
            "above_sma200": bool(current > sma200) if sma200 and pd.notna(sma200) else None,
            "golden_cross_state": (bool(sma50 > sma200)
                                   if sma200 and pd.notna(sma50) and pd.notna(sma200) else None),
            "verification": ESTIMATED + " (computed from verified history)",
        },
        "momentum": {"rsi_14": _rsi(close), "verification": ESTIMATED + " (computed from verified history)"},
        "volatility": {
            "atr_14": _atr(hist),
            "realized_vol_30d_ann_pct": _f(rets.tail(30).std() * np.sqrt(252) * 100, 2),
            "verification": ESTIMATED + " (computed from verified history)",
        },
        "range_52w": {
            "high": _f(hi52), "low": _f(lo52),
            "position_pct": _f((current - lo52) / (hi52 - lo52) * 100, 1) if hi52 > lo52 else None,
            "verification": ESTIMATED + " (computed from verified history)",
        },
        "support_resistance": {**_swings(hist),
                               "verification": ESTIMATED + " (swing levels from verified history)"},
    })

    try:
        out["reference_bands"] = _mc_bands(close, current)
    except Exception as exc:  # noqa: BLE001
        log.warning("MC bands failed for %s: %s", key, exc)
        out["reference_bands"] = {"status": "FAILED", "verification": UNVERIFIED}
    return out


def analyze(keys: list[str] | None = None) -> dict:
    return {k: analyze_one(k) for k in (keys or list(COMMODITIES))}


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    keys = sys.argv[1:] or ["wti", "gold", "copper"]
    print(json.dumps(analyze(keys), indent=2, default=str))
