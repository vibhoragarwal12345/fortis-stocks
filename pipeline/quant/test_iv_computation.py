"""
Validation suite for the Black-Scholes implied-volatility pipeline.

Run this BEFORE trusting fetch_atm_iv anywhere downstream:
  [A] synthetic round-trip  -- BS price -> invert -> must recover the input vol
  [B] real-data sanity      -- 10 liquid names must land in a believable range
  [C] put-call parity       -- ATM call IV and put IV must agree closely
  [D] VIX cross-check       -- SPY 30-day IV should sit near the VIX
  plus the diff vs yfinance's broken `impliedVolatility` field.

Usage:
    python pipeline/quant/test_iv_computation.py
"""

import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quant.utils import (  # noqa: E402
    black_scholes_call_price, fetch_atm_iv, implied_volatility_from_price,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("iv_test")

SANITY_TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY",
                  "QQQ", "JPM", "AMZN", "GOOGL", "META"]
ORIGINAL_FAILURES = ["AAPL", "ENPH", "CAR", "PZZA"]   # the screenshot tickers
# Loose plausibility expectations (low, high) for [B].
EXPECTED = {"AAPL": (0.15, 0.45), "SPY": (0.08, 0.30), "QQQ": (0.10, 0.35),
            "TSLA": (0.30, 0.90), "NVDA": (0.25, 0.80)}


def _isnan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _pct(x, nd=1):
    return f"{x * 100:.{nd}f}%" if not _isnan(x) else "FAILED"


# ──────────────────────────────────────────────────────────────────────────────
# [A] synthetic round-trip
# ──────────────────────────────────────────────────────────────────────────────

def test_synthetic() -> bool:
    print("\n" + "=" * 78)
    print("[A] SYNTHETIC ROUND-TRIP TEST")
    print("=" * 78)
    S, K, T, r, sigma = 100.0, 100.0, 30 / 365.25, 0.05, 0.25
    price = black_scholes_call_price(S, K, T, r, sigma)
    recovered = implied_volatility_from_price(price, S, K, T, r, "call")
    err = abs(recovered - sigma)
    ok = err < 1e-4
    print(f"  BS call price for sigma=0.25 (S=K=100, T~30d, r=5%): ${price:.4f}")
    print(f"  implied_volatility_from_price recovered: {recovered:.8f}")
    print(f"  error vs input 0.25: {err:.2e}  (tolerance 1e-4)")
    print(f"  --> {'PASS' if ok else 'FAIL'}")
    return ok


# ──────────────────────────────────────────────────────────────────────────────
# the OLD broken approach -- yfinance's impliedVolatility field, for the diff
# ──────────────────────────────────────────────────────────────────────────────

def broken_yf_iv(ticker: str):
    try:
        tk = yf.Ticker(ticker)
        exps = list(tk.options or [])
        if not exps:
            return None
        today = datetime.now(timezone.utc).date()
        exp = min(exps, key=lambda e: abs(
            (datetime.strptime(e, "%Y-%m-%d").date() - today).days - 30))
        spot = float(tk.history(period="5d")["Close"].dropna().iloc[-1])
        calls = tk.option_chain(exp).calls
        row = calls.iloc[(calls["strike"] - spot).abs().to_numpy().argmin()]
        return float(row["impliedVolatility"])
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# [B]/[C] real-data IV + parity
# ──────────────────────────────────────────────────────────────────────────────

def test_real(tickers: list[str], label: str) -> list[tuple]:
    print("\n" + "=" * 78)
    print(f"[B/C] REAL-DATA IV + PUT-CALL PARITY -- {label}")
    print("=" * 78)
    rows = []
    for t in tickers:
        meta = fetch_atm_iv(t)
        broken = broken_yf_iv(t)
        rows.append((t, meta, broken))

    print(f"\n  {'TICKER':<8}{'COMPUTED IV':>13}{'CALL IV':>10}{'PUT IV':>10}"
          f"{'PARITY':>9}{'QUALITY':>11}{'BROKEN YF':>11}{'  DTE/STRIKE'}")
    for t, m, broken in rows:
        dte = m["days_to_expiry"]
        strike = m["strike"]
        ds = f"  {dte}d @ ${strike}" if dte and strike else ""
        par = _pct(m["parity_spread"]) if m["parity_spread"] is not None else "n/a"
        print(f"  {t:<8}{_pct(m['iv']):>13}{_pct(m['call_iv']):>10}"
              f"{_pct(m['put_iv']):>10}{par:>9}{m['quality']:>11}"
              f"{_pct(broken, 2):>11}{ds}")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# [D] VIX cross-check
# ──────────────────────────────────────────────────────────────────────────────

def test_vix_crosscheck(spy_meta: dict) -> None:
    print("\n" + "=" * 78)
    print("[D] SPY 30-DAY IV vs VIX CROSS-CHECK")
    print("=" * 78)
    try:
        vix = float(yf.Ticker("^VIX").history(period="5d")["Close"].dropna().iloc[-1])
    except Exception as exc:
        print(f"  VIX fetch failed: {exc}")
        return
    spy_iv = spy_meta.get("iv")
    print(f"  VIX (30-day SPX implied vol):      {vix:.2f}%")
    if _isnan(spy_iv):
        print("  SPY computed IV: FAILED -- cannot compare")
        return
    spy_pct = spy_iv * 100
    print(f"  Our computed SPY 30-day ATM IV:    {spy_pct:.2f}%")
    print(f"  Spread (SPY IV - VIX):             {spy_pct - vix:+.2f} vol pts")
    close = abs(spy_pct - vix) <= 5.0
    print(f"  --> {'PASS' if close else 'WIDE'} -- SPY ATM IV and VIX should be "
          f"within a few points (SPY/SPX + convexity differences expected)")


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────

def run() -> None:
    synth_ok = test_synthetic()

    sanity = test_real(SANITY_TICKERS, "10 LIQUID SANITY-CHECK NAMES")
    failing = test_real(ORIGINAL_FAILURES, "ORIGINAL FAILING TICKERS (screenshot)")

    spy_meta = next((m for t, m, _ in sanity if t == "SPY"), {})
    test_vix_crosscheck(spy_meta)

    # ── range + parity verdicts ───────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("[B] RANGE CHECK (computed IV must be 10%-100%)  +  "
          "[C] PARITY VERDICT")
    print("=" * 78)
    range_fail, parity_fail = [], []
    for t, m, _ in sanity:
        iv = m["iv"]
        if _isnan(iv):
            range_fail.append((t, "IV computation failed"))
        elif not (0.10 <= iv <= 1.00):
            range_fail.append((t, f"IV {iv*100:.1f}% outside 10%-100%"))
        lo_hi = EXPECTED.get(t)
        if lo_hi and not _isnan(iv) and not (lo_hi[0] <= iv <= lo_hi[1]):
            range_fail.append((t, f"IV {iv*100:.1f}% outside expected "
                                  f"{lo_hi[0]*100:.0f}-{lo_hi[1]*100:.0f}%"))
        ps = m["parity_spread"]
        if ps is not None and ps > 0.02:
            parity_fail.append((t, f"call/put IV {ps*100:.1f} pts apart"))
    print(f"  Range check: {len(SANITY_TICKERS) - len({t for t,_ in range_fail})}"
          f"/{len(SANITY_TICKERS)} within range")
    for t, why in range_fail:
        print(f"    CONCERN  {t}: {why}")
    print(f"  Parity (>2% spread flagged): "
          f"{'all tight' if not parity_fail else ''}")
    for t, why in parity_fail:
        print(f"    NOTE  {t}: {why}")

    # ── quality-check failures ────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("FAILURES / QUALITY FLAGS")
    print("=" * 78)
    any_flag = False
    for t, m, _ in sanity + failing:
        if m["quality"] in ("failed", "low"):
            any_flag = True
            reason = m["reason_if_failed"] or f"quality={m['quality']}"
            print(f"  {m['quality'].upper():<8} {t:<8} {reason}")
    if not any_flag:
        print("  (none -- every ticker computed at high/medium quality)")

    # ── diff vs the broken field ──────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("DIFF: yfinance broken `impliedVolatility` vs our computed IV")
    print("=" * 78)
    print(f"  {'TICKER':<8}{'BROKEN YF':>12}{'COMPUTED':>12}{'  MAGNITUDE OF ERROR'}")
    for t, m, broken in sanity + failing:
        if _isnan(m["iv"]):
            continue
        if broken is None:
            print(f"  {t:<8}{'n/a':>12}{_pct(m['iv']):>12}")
            continue
        factor = (m["iv"] / broken) if broken > 1e-6 else float("inf")
        fac = f"{factor:.0f}x too low" if factor < float("inf") else "broken=0"
        print(f"  {t:<8}{_pct(broken,2):>12}{_pct(m['iv']):>12}   yfinance was {fac}")

    print("\n" + "=" * 78)
    verdict = "PASS" if synth_ok and not range_fail else "REVIEW NEEDED"
    print(f"OVERALL: synthetic={'PASS' if synth_ok else 'FAIL'} | "
          f"range concerns={len(range_fail)} | --> {verdict}")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    run()
