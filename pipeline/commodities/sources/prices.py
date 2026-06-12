"""
Commodity futures prices — yfinance front-month continuous contracts, with an
independent FRED spot cross-check per commodity.

Output per commodity:
  price, OHLCV, change %, asof timestamp (delayed data — yfinance futures are
  delayed ~10-15 min), 1y history summary, and a cross-check against the FRED
  spot/index series from the registry. Daily FRED series get a divergence %;
  monthly IMF index series are sanity context only (different units/basis) and
  are labeled as such rather than compared numerically.

Iron ore/steel has no reliable free future on yfinance: the FRED monthly IMF
index becomes the primary price, flagged low-frequency; anything more
real-time is UNVERIFIED.

Usage:  python pipeline/commodities/sources/prices.py   # smoke test
"""

import json
import logging
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.registry import COMMODITIES  # noqa: E402
from commodities.sources.common import (  # noqa: E402
    ESTIMATED, UNVERIFIED, fred_latest, utcnow_iso, verified,
)
from config import FRED_API_KEY  # noqa: E402

log = logging.getLogger("commodities.prices")

HISTORY_PERIOD = "1y"


def _f(v):
    try:
        v = float(v)
        return None if pd.isna(v) else round(v, 4)
    except (TypeError, ValueError):
        return None


def _fetch_one(key: str, spec: dict) -> dict:
    out: dict = {
        "commodity": key,
        "name": spec["name"],
        "category": spec["category"],
        "fetched_at": utcnow_iso(),
        "data_delay_note": "yfinance futures quotes are delayed; treat asof as indicative, not real-time",
    }

    # ── yfinance future ────────────────────────────────────────────────────
    sym = spec["yf_symbol"]
    hist = pd.DataFrame()
    if sym is None:
        out["future"] = {
            "status": "NO_FREE_FUTURE",
            "verification": UNVERIFIED,
            "note": "No reliable free futures quote on yfinance for this commodity; "
                    "FRED monthly index below is the primary price (low-frequency).",
        }
    else:
        try:
            hist = yf.Ticker(sym).history(period=HISTORY_PERIOD, auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            hist = pd.DataFrame()
            log.warning("yfinance %s failed: %s", sym, exc)
        if hist.empty:
            out["future"] = {"status": "FETCH_FAILED", "symbol": sym, "verification": UNVERIFIED}
        else:
            last = hist.iloc[-1]
            prev_close = _f(hist["Close"].iloc[-2]) if len(hist) > 1 else None
            close = _f(last["Close"])
            out["future"] = {
                "status": "OK",
                "symbol": sym,
                "asof": str(hist.index[-1].date()),
                "open": _f(last["Open"]), "high": _f(last["High"]),
                "low": _f(last["Low"]), "close": close,
                "volume": int(last["Volume"]) if pd.notna(last["Volume"]) else None,
                "change_pct_1d": _f((close - prev_close) / prev_close * 100)
                                 if close and prev_close else None,
                "history_1y": {
                    "bars": len(hist),
                    "high_52w": _f(hist["High"].max()),
                    "low_52w": _f(hist["Low"].min()),
                    "first_date": str(hist.index[0].date()),
                    "verification": verified(f"yfinance {sym} {HISTORY_PERIOD} daily"),
                },
                "verification": verified(f"yfinance {sym}"),
            }

    # ── FRED cross-check ───────────────────────────────────────────────────
    fs = spec["fred_spot"]
    obs = fred_latest(fs["series"], FRED_API_KEY) if fs else None
    if not obs:
        out["fred_crosscheck"] = {
            "status": "UNAVAILABLE",
            "series": fs["series"] if fs else None,
            "verification": UNVERIFIED,
            "note": "FRED series returned nothing (missing key, discontinued series, or fetch failure).",
        }
    else:
        cc = {
            "status": "OK",
            "series": fs["series"],
            "label": fs["label"],
            "frequency": fs["freq"],
            "value": obs["value"],
            "date": obs["date"],
            "verification": verified(f"FRED {fs['series']}"),
        }
        fut = out.get("future", {})
        if fs["freq"] == "daily" and fut.get("status") == "OK" and fut.get("close"):
            # Compare same-date: FRED spot lags days; in a fast tape, latest
            # future vs stale spot fabricates divergence. Use the future's
            # close on the FRED observation date when we have that bar.
            fut_on_date = None
            try:
                dates = hist.index.strftime("%Y-%m-%d")
                if obs["date"] in set(dates):
                    fut_on_date = _f(hist["Close"][dates == obs["date"]].iloc[-1])
            except Exception:  # noqa: BLE001
                pass
            ref = fut_on_date if fut_on_date else fut["close"]
            cc["divergence_vs_future_pct"] = _f((ref - obs["value"]) / obs["value"] * 100)
            cc["divergence_basis"] = (
                f"future close on {obs['date']} (same-date comparison)"
                if fut_on_date else "latest future close (no same-date bar; date mismatch inflates the gap)"
            )
            cc["divergence_verification"] = ESTIMATED + " (computed from verified figures; residual gap is spot-vs-front-month basis, i.e. curve structure)"
        elif fs["freq"] == "monthly":
            cc["note"] = ("Monthly IMF/global index — different basis and lag vs the future; "
                          "sanity context only, not a tick comparison.")
        out["fred_crosscheck"] = cc

    return out


def fetch_all() -> dict:
    """All commodities -> price payloads. Never raises; gaps are labeled."""
    return {key: _fetch_one(key, spec) for key, spec in COMMODITIES.items()}


def smoke() -> dict:
    data = fetch_all()
    ok_fut = [k for k, v in data.items() if v.get("future", {}).get("status") == "OK"]
    ok_fred = [k for k, v in data.items() if v.get("fred_crosscheck", {}).get("status") == "OK"]
    return {
        "source": "prices (yfinance futures + FRED cross-check)",
        "needs_key": "FRED_API_KEY (present)" if FRED_API_KEY else "FRED_API_KEY MISSING",
        "futures_ok": ok_fut,
        "futures_failed_or_na": [k for k in data if k not in ok_fut],
        "fred_crosscheck_ok": ok_fred,
        "fred_crosscheck_failed": [k for k in data if k not in ok_fred],
        "raw": data,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    print(json.dumps(smoke(), indent=2, default=str))
