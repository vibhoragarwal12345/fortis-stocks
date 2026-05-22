"""
Quantitative Models Desk -- shared utilities.
Data fetching, validation and return computation. Deterministic only.
"""

import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from scipy.optimize import brentq
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import FRED_API_KEY  # noqa: E402
from quant.models_config import MIN_DATA_REQUIRED_DAYS, TRADING_DAYS_PER_YEAR  # noqa: E402

log = logging.getLogger(__name__)

_DEFAULT_RF = 0.04   # last-resort risk-free fallback if FRED and yfinance both fail


class InsufficientDataError(Exception):
    """Raised when a ticker lacks enough price history to model safely."""


# ══════════════════════════════════════════════════════════════════════════════
# Price data
# ══════════════════════════════════════════════════════════════════════════════

def fetch_price_history(ticker: str, lookback_days: int = 252) -> pd.Series:
    """Daily adjusted close for `ticker`. Extra calendar days are requested so
    that `lookback_days` *trading* days are available."""
    period_days = int(lookback_days * 1.5) + 40
    df = yf.download(ticker, period=f"{period_days}d", interval="1d",
                     auto_adjust=True, progress=False)
    if df is None or df.empty:
        raise InsufficientDataError(f"{ticker}: no price data returned")
    close = df["Close"].dropna()
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    return close.tail(lookback_days)


def validate_data_sufficiency(prices: pd.Series,
                              min_days: int = MIN_DATA_REQUIRED_DAYS) -> None:
    """Raise InsufficientDataError if `prices` has fewer than `min_days` points."""
    n = 0 if prices is None else len(pd.Series(prices).dropna())
    if n < min_days:
        raise InsufficientDataError(
            f"insufficient history: {n} days < required {min_days}")


def safe_log_returns(prices: pd.Series) -> pd.Series:
    """Log returns, robust to zero / missing / non-positive prices."""
    p = pd.Series(prices).astype(float)
    p = p.replace([0.0, np.inf, -np.inf], np.nan).dropna()
    p = p[p > 0]
    if len(p) < 2:
        return pd.Series(dtype=float)
    return np.log(p / p.shift(1)).dropna()


# ══════════════════════════════════════════════════════════════════════════════
# Market context
# ══════════════════════════════════════════════════════════════════════════════

def get_risk_free_rate() -> float:
    """Latest 3-month T-bill rate as a decimal (e.g. 0.043).

    FRED series DGS3MO is authoritative; yfinance ^IRX (13-week T-bill) is a
    key-free fallback; a static default is the last resort.
    """
    if FRED_API_KEY:
        try:
            resp = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": "DGS3MO", "api_key": FRED_API_KEY,
                        "file_type": "json", "sort_order": "desc", "limit": 10},
                timeout=20)
            resp.raise_for_status()
            for obs in resp.json().get("observations", []):
                if obs.get("value") not in (".", "", None):
                    return round(float(obs["value"]) / 100.0, 5)
        except Exception as exc:
            log.warning("FRED DGS3MO failed (%s) -- trying yfinance ^IRX", exc)
    try:
        irx = yf.download("^IRX", period="10d", interval="1d",
                          auto_adjust=True, progress=False)["Close"].dropna()
        if not irx.empty:
            val = float(irx.iloc[-1])
            return round(val / 100.0, 5)
    except Exception as exc:
        log.warning("yfinance ^IRX failed (%s) -- using default rf", exc)
    return _DEFAULT_RF


def get_market_returns(lookback_days: int = 252) -> pd.Series:
    """SPY log returns as the market proxy."""
    prices = fetch_price_history("SPY", lookback_days)
    return safe_log_returns(prices)


def annualize_vol(daily_returns: pd.Series) -> float:
    """Annualized standard deviation of daily returns."""
    return float(pd.Series(daily_returns).std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


# ══════════════════════════════════════════════════════════════════════════════
# Implied volatility -- computed by inverting Black-Scholes on market prices.
#
# yfinance's own `impliedVolatility` column is unreliable -- it returns dyadic
# artifacts (0.0156, 0.0313, ... = powers of 1/64) instead of real volatility.
# So we never read it: we take the option's bid/ask mid price and solve for the
# volatility that reproduces it. This is how institutional systems do it.
# ══════════════════════════════════════════════════════════════════════════════

def black_scholes_call_price(S, K, T, r, sigma, q=0.0):
    """Black-Scholes price of a European CALL option.

    C = S e^{-qT} N(d1) - K e^{-rT} N(d2)
        d1 = [ln(S/K) + (r - q + sigma^2/2) T] / (sigma sqrt(T))
        d2 = d1 - sigma sqrt(T)
    where N() is the standard-normal CDF, q the dividend yield.
    """
    if T <= 0:                                  # expired -> intrinsic value
        return max(S - K, 0.0)
    if sigma <= 0:                              # no vol -> discounted-forward intrinsic
        return max(S * math.exp(-q * T) - K * math.exp(-r * T), 0.0)
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return (S * math.exp(-q * T) * norm.cdf(d1)
            - K * math.exp(-r * T) * norm.cdf(d2))


def black_scholes_put_price(S, K, T, r, sigma, q=0.0):
    """Black-Scholes price of a European PUT option.

    P = K e^{-rT} N(-d2) - S e^{-qT} N(-d1)   (same d1/d2 as the call).
    """
    if T <= 0:
        return max(K - S, 0.0)
    if sigma <= 0:
        return max(K * math.exp(-r * T) - S * math.exp(-q * T), 0.0)
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return (K * math.exp(-r * T) * norm.cdf(-d2)
            - S * math.exp(-q * T) * norm.cdf(-d1))


def implied_volatility_from_price(option_price, S, K, T, r,
                                  option_type="call", q=0.0) -> float:
    """Invert Black-Scholes to recover the volatility implied by `option_price`.

    Uses Brent's method (scipy.optimize.brentq) over a 0.1%-500% vol bracket.
    Returns NaN -- never a fabricated value -- on any of:
      * an arbitrage-violating / stale price (below intrinsic, above the bound),
      * non-convergence,
      * a solved IV outside the plausible (5%, 300%) range.
    """
    if not (T > 0 and option_price > 0 and S > 0 and K > 0):
        return float("nan")

    if option_type == "call":
        lower = max(0.0, S * math.exp(-q * T) - K * math.exp(-r * T))
        upper = S * math.exp(-q * T)                 # a call can't exceed disc. spot
        price_fn = black_scholes_call_price
    else:
        lower = max(0.0, K * math.exp(-r * T) - S * math.exp(-q * T))
        upper = K * math.exp(-r * T)                 # a put can't exceed disc. strike
        price_fn = black_scholes_put_price

    # Arbitrage check: the price must sit between intrinsic and the hard bound.
    if option_price < lower - 1e-6 or option_price > upper + 1e-6:
        return float("nan")

    objective = lambda sigma: price_fn(S, K, T, r, sigma, q) - option_price
    try:
        lo, hi = 0.001, 5.0                          # 0.1% .. 500% volatility
        if objective(lo) > 0 or objective(hi) < 0:   # price not bracketed
            return float("nan")
        iv = brentq(objective, lo, hi, maxiter=200, xtol=1e-8)
    except (ValueError, RuntimeError):
        return float("nan")
    if not (0.05 < iv < 3.0):                        # outside a believable range
        return float("nan")
    return float(iv)


def _valid_option_quote(row) -> bool:
    """A usable quote: two-sided market, a non-blown-out spread, real liquidity."""
    bid = float(row.get("bid") or 0.0)
    ask = float(row.get("ask") or 0.0)
    if bid <= 0 or ask <= 0 or ask < bid:
        return False
    if (ask - bid) > 0.5 * ask:                      # spread wider than half the ask
        return False
    vol = float(row.get("volume") or 0.0)
    oi = float(row.get("openInterest") or 0.0)
    return vol > 0 or oi > 10


def fetch_atm_iv(ticker: str, target_dte_days: int = 30) -> dict:
    """At-the-money implied volatility for `ticker`, computed by inverting
    Black-Scholes on the bid/ask midpoint -- NOT read from yfinance's broken
    `impliedVolatility` field.

    Returns a metadata dict (never raises):
        iv, spot, strike, expiration, days_to_expiry, call_iv, put_iv,
        parity_spread, quality ('high'|'medium'|'low'|'failed'), reason_if_failed
    """
    meta = {"iv": float("nan"), "spot": None, "strike": None, "expiration": None,
            "days_to_expiry": None, "call_iv": float("nan"),
            "put_iv": float("nan"), "parity_spread": None,
            "quality": "failed", "reason_if_failed": None}

    def _fail(reason):
        meta["reason_if_failed"] = reason
        log.info("IV %-6s FAILED -- %s", ticker, reason)
        return meta

    try:
        tk = yf.Ticker(ticker)

        # (a) spot price -------------------------------------------------------
        hist = tk.history(period="5d")
        if hist is None or hist.empty:
            return _fail("no spot price available")
        spot = float(hist["Close"].dropna().iloc[-1])
        meta["spot"] = round(spot, 4)

        # (b) expiration closest to target_dte_days ---------------------------
        expirations = list(tk.options or [])
        if not expirations:
            return _fail("no options chain")
        today = datetime.now(timezone.utc).date()
        best, best_gap = None, 10 ** 9
        for exp in expirations:
            try:
                dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
            except ValueError:
                continue
            if abs(dte - target_dte_days) < best_gap:
                best_gap, best = abs(dte - target_dte_days), (exp, dte)
        if best is None:
            return _fail("no parseable expirations")
        exp, dte = best
        T = dte / 365.25
        if T < 0.02:
            return _fail(f"nearest expiration {dte}d -- too close (<7d, noisy)")
        if T > 0.5:
            return _fail(f"nearest expiration {dte}d -- too far (>6mo)")
        meta["expiration"], meta["days_to_expiry"] = exp, dte

        # (c) chain ------------------------------------------------------------
        chain = tk.option_chain(exp)
        calls, puts = chain.calls, chain.puts
        if calls is None or puts is None or calls.empty or puts.empty:
            return _fail("empty call or put chain")
        rf = get_risk_free_rate()

        # (d-e) ATM strike with valid two-sided quotes -- search up to 3 away --
        common = sorted(set(calls["strike"]).intersection(puts["strike"]),
                        key=lambda k: abs(k - spot))
        if not common:
            return _fail("no strikes common to calls and puts")
        chosen, relaxed = None, None
        for strike in common[:4]:                    # ATM + up to 3 strikes away
            crow = calls[calls["strike"] == strike]
            prow = puts[puts["strike"] == strike]
            if crow.empty or prow.empty:
                continue
            crow, prow = crow.iloc[0], prow.iloc[0]
            if _valid_option_quote(crow) and _valid_option_quote(prow):
                chosen = (strike, crow, prow)
                break
            if relaxed is None and (_valid_option_quote(crow)
                                    or _valid_option_quote(prow)):
                relaxed = (strike, crow, prow)
        if chosen is None:
            chosen = relaxed                         # accept a one-sided strike
        if chosen is None:
            return _fail("no strike within 4 of ATM has a valid bid/ask quote")
        strike, crow, prow = chosen
        meta["strike"] = round(float(strike), 2)
        dist_pct = abs(strike - spot) / spot * 100.0

        # (f) bid/ask midpoints ------------------------------------------------
        def _mid(row):
            bid, ask = float(row.get("bid") or 0.0), float(row.get("ask") or 0.0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0, bid, ask
            last = float(row.get("lastPrice") or 0.0)
            return (last if last > 0 else None), bid, ask

        call_mid, c_bid, c_ask = _mid(crow)
        put_mid, p_bid, p_ask = _mid(prow)

        # (g-h) invert Black-Scholes ------------------------------------------
        iv_call = (implied_volatility_from_price(call_mid, spot, strike, T, rf,
                                                 "call") if call_mid else float("nan"))
        iv_put = (implied_volatility_from_price(put_mid, spot, strike, T, rf,
                                                "put") if put_mid else float("nan"))
        meta["call_iv"] = round(iv_call, 4) if not math.isnan(iv_call) else float("nan")
        meta["put_iv"] = round(iv_put, 4) if not math.isnan(iv_put) else float("nan")

        log.info("IV %-6s spot $%.2f | exp %s (%dd) | strike $%.2f (%.1f%% from "
                 "spot) | call mid $%.2f [%.2f/%.2f] put mid $%.2f [%.2f/%.2f] | "
                 "call_iv %s put_iv %s", ticker, spot, exp, dte, strike, dist_pct,
                 call_mid or 0, c_bid, c_ask, put_mid or 0, p_bid, p_ask,
                 f"{iv_call:.3f}" if not math.isnan(iv_call) else "nan",
                 f"{iv_put:.3f}" if not math.isnan(iv_put) else "nan")

        valid_c, valid_p = not math.isnan(iv_call), not math.isnan(iv_put)
        if not valid_c and not valid_p:
            return _fail("Black-Scholes inversion failed on both call and put")

        # (i-j) put-call parity validation + averaging ------------------------
        if valid_c and valid_p:
            spread = abs(iv_call - iv_put)
            meta["parity_spread"] = round(spread, 4)
            if spread <= 0.02:
                meta["iv"], quality = round((iv_call + iv_put) / 2, 4), "high"
            elif spread <= 0.05:
                meta["iv"], quality = round((iv_call + iv_put) / 2, 4), "medium"
            else:
                # >5 vol points apart -- one quote is stale; take the liquid leg.
                c_vol = float(crow.get("volume") or 0.0)
                p_vol = float(prow.get("volume") or 0.0)
                if c_vol > 0 or p_vol > 0:
                    meta["iv"] = round(iv_call if c_vol >= p_vol else iv_put, 4)
                    quality = "low"
                else:
                    return _fail(f"call/put IV disagree by {spread:.2f} vol pts, "
                                 f"both legs illiquid")
        else:
            meta["iv"] = round(iv_call if valid_c else iv_put, 4)
            quality = "medium"                       # only one leg solved

        # Strike too far from spot is not a true ATM read -- cap the quality.
        if dist_pct > 5.0 and quality in ("high", "medium"):
            quality = "low"
        meta["quality"] = quality

        log.info("IV %-6s -> %.1f%% (quality=%s, parity_spread=%s)",
                 ticker, meta["iv"] * 100, quality,
                 meta["parity_spread"] if meta["parity_spread"] is not None else "n/a")
        return meta
    except Exception as exc:
        return _fail(f"unexpected error: {exc}")
