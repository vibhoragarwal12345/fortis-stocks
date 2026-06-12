"""
Agent 5: seasonality — calendar-month tendencies COMPUTED from up to 10 years
of actual price history (yfinance monthly bars), never asserted from lore.

Per commodity: for each calendar month, the average historical return and the
hit rate (share of years positive), plus the read for the current and next
month. Output is labeled a STATISTICAL TENDENCY, NOT A PREDICTION, and months
with weak hit rates (45-60%) are explicitly called "no reliable tendency".

The folk patterns (natgas winter draw, harvest pressure) only appear here if
the data this run actually shows them. Iron ore uses the monthly FRED series
when available; if not, seasonality is UNVERIFIED.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.registry import COMMODITIES  # noqa: E402
from commodities.sources.common import (  # noqa: E402
    ESTIMATED, UNVERIFIED, fred_observations, utcnow_iso,
)
from config import FRED_API_KEY  # noqa: E402

log = logging.getLogger("commodities.seasonality")

LOOKBACK = "10y"
MIN_YEARS = 5
TENDENCY_LABEL = "STATISTICAL TENDENCY computed from historical monthly returns — NOT a prediction"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _monthly_closes(key: str) -> tuple[pd.Series | None, str]:
    sym = COMMODITIES[key]["yf_symbol"]
    if sym:
        try:
            hist = yf.Ticker(sym).history(period=LOOKBACK, interval="1mo",
                                          auto_adjust=False)
            if not hist.empty:
                return hist["Close"].dropna(), f"yfinance {sym} monthly {LOOKBACK}"
        except Exception as exc:  # noqa: BLE001
            log.warning("yfinance monthly %s failed: %s", sym, exc)
    fs = COMMODITIES[key].get("fred_spot")
    if fs and fs["freq"] == "monthly":
        obs = fred_observations(fs["series"], FRED_API_KEY, limit=130)
        if obs:
            s = pd.Series({pd.Timestamp(o["date"]): o["value"] for o in obs}).sort_index()
            return s, f"FRED {fs['series']} monthly"
    return None, ""


def _month_stats(rets: pd.Series) -> list[dict]:
    out = []
    for m in range(1, 13):
        r = rets[rets.index.month == m]
        if len(r) < MIN_YEARS:
            out.append({"month": MONTHS[m - 1], "years": len(r), "tendency": "insufficient_history"})
            continue
        avg = float(r.mean()) * 100
        hit = float((r > 0).mean()) * 100
        if hit >= 65 and avg > 0:
            tendency = "historically_positive"
        elif hit <= 35 and avg < 0:
            tendency = "historically_negative"
        else:
            tendency = "no_reliable_tendency"
        out.append({"month": MONTHS[m - 1], "years": len(r),
                    "avg_return_pct": round(avg, 2), "hit_rate_pct": round(hit, 1),
                    "tendency": tendency})
    return out


def analyze_one(key: str) -> dict:
    out: dict = {"commodity": key, "name": COMMODITIES[key]["name"],
                 "computed_at": utcnow_iso(), "label": TENDENCY_LABEL}
    closes, source = _monthly_closes(key)
    if closes is None or len(closes) < MIN_YEARS * 12 // 2:
        out.update({"status": "INSUFFICIENT_DATA", "verification": UNVERIFIED,
                    "note": "Not enough monthly history fetched to compute tendencies."})
        return out

    rets = np.log(closes / closes.shift()).dropna()
    stats = _month_stats(rets)
    now = datetime.now(timezone.utc)
    cur, nxt = now.month, now.month % 12 + 1
    out.update({
        "status": "OK",
        "source": source,
        "months": stats,
        "current_month": stats[cur - 1],
        "next_month": stats[nxt - 1],
        "verification": ESTIMATED + f" (computed from {len(rets)} verified monthly returns, {source})",
    })
    return out


def analyze(keys: list[str] | None = None) -> dict:
    return {k: analyze_one(k) for k in (keys or list(COMMODITIES))}


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    keys = sys.argv[1:] or ["wti", "gold", "copper"]
    print(json.dumps(analyze(keys), indent=2, default=str))
