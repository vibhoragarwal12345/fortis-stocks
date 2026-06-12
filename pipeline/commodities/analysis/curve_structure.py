"""
Agent 3: futures curve structure — contango vs backwardation from the strip.

Method: construct Yahoo contract symbols (ROOT + month code + YY + .EXCHANGE,
e.g. CLZ26.NYM) for the next ~12 listed delivery months of each commodity's
standard cycle, fetch recent closes for all of them in one batch, and keep
whichever contracts actually return data. The curve read is computed only
from contracts verified this run; if fewer than 3 months come back the curve
is UNVERIFIED — never extrapolated.

Signal convention (stated in output):
  far below near  -> BACKWARDATION  -> tight prompt supply / bullish signal
  far above near  -> CONTANGO       -> oversupply / storage economics
  annualized slope within ±2%/yr    -> FLAT

Iron ore has no free strip (SGX/DCE) -> UNVERIFIED by design.
"""

import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.registry import COMMODITIES  # noqa: E402
from commodities.sources.common import ESTIMATED, UNVERIFIED, utcnow_iso, verified  # noqa: E402

log = logging.getLogger("commodities.curve")

MONTH_CODES = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
               7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}

# root, yahoo exchange suffix, listed delivery-month cycle
CONTRACT_SPECS = {
    "wti":      ("CL", "NYM", list(range(1, 13))),
    "brent":    ("BZ", "NYM", list(range(1, 13))),
    "natgas":   ("NG", "NYM", list(range(1, 13))),
    "gold":     ("GC", "CMX", [2, 4, 6, 8, 10, 12]),
    "silver":   ("SI", "CMX", [1, 3, 5, 7, 9, 12]),
    "copper":   ("HG", "CMX", [1, 3, 5, 7, 9, 12]),
    "soybeans": ("ZS", "CBT", [1, 3, 5, 7, 8, 9, 11]),
    "corn":     ("ZC", "CBT", [3, 5, 7, 9, 12]),
    "wheat":    ("ZW", "CBT", [3, 5, 7, 9, 12]),
    "coffee":   ("KC", "NYB", [3, 5, 7, 9, 12]),
}

N_CANDIDATES = 12
FLAT_BAND_ANN_PCT = 2.0


def _candidate_symbols(key: str, today: date) -> list[tuple[str, date]]:
    root, exch, cycle = CONTRACT_SPECS[key]
    out = []
    y, m = today.year, today.month
    while len(out) < N_CANDIDATES:
        m += 1
        if m > 12:
            m, y = 1, y + 1
        if m in cycle:
            out.append((f"{root}{MONTH_CODES[m]}{str(y)[-2:]}.{exch}",
                        date(y, m, 1)))
    return out


def _fetch_strip(symbols: list[tuple[str, date]]) -> list[dict]:
    tickers = [s for s, _ in symbols]
    try:
        data = yf.download(tickers, period="5d", auto_adjust=False,
                           progress=False, group_by="ticker", threads=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("strip download failed: %s", exc)
        return []
    strip = []
    for sym, dmonth in symbols:
        try:
            closes = (data[sym]["Close"] if len(tickers) > 1 else data["Close"]).dropna()
            if closes.empty:
                continue
            strip.append({"symbol": sym, "delivery_month": dmonth.isoformat()[:7],
                          "close": round(float(closes.iloc[-1]), 4),
                          "asof": str(closes.index[-1].date()),
                          "verification": verified(f"yfinance {sym}")})
        except (KeyError, TypeError):
            continue
    return strip


def analyze_one(key: str, today: date | None = None) -> dict:
    spec = COMMODITIES[key]
    out: dict = {"commodity": key, "name": spec["name"], "computed_at": utcnow_iso()}
    if key not in CONTRACT_SPECS:
        out.update({
            "status": "NO_FREE_STRIP", "verification": UNVERIFIED,
            "note": "No free futures strip (contract trades on SGX/DCE) — curve "
                    "structure unknown, not estimated.",
        })
        return out

    strip = _fetch_strip(_candidate_symbols(key, today or date.today()))
    if len(strip) < 3:
        out.update({
            "status": "INSUFFICIENT_STRIP", "contracts_returned": len(strip),
            "strip": strip, "verification": UNVERIFIED,
            "note": "Fewer than 3 contract months returned data — curve not computed "
                    "rather than extrapolated.",
        })
        return out

    near, far = strip[0], strip[-1]
    months_span = ((int(far["delivery_month"][:4]) - int(near["delivery_month"][:4])) * 12
                   + int(far["delivery_month"][5:7]) - int(near["delivery_month"][5:7]))
    slope_pct = (far["close"] - near["close"]) / near["close"] * 100
    ann_slope = slope_pct / months_span * 12 if months_span else 0.0

    if ann_slope <= -FLAT_BAND_ANN_PCT:
        structure, signal = "backwardation", "tight prompt supply — bullish near-term signal"
    elif ann_slope >= FLAT_BAND_ANN_PCT:
        structure, signal = "contango", "oversupply / storage economics — bearish-to-neutral signal"
    else:
        structure, signal = "flat", "no strong curve signal"

    out.update({
        "status": "OK",
        "structure": structure,
        "conventional_signal": signal,
        "slope": {
            "near": near, "far": far, "months_span": months_span,
            "far_vs_near_pct": round(slope_pct, 2),
            "annualized_slope_pct": round(ann_slope, 2),
            "flat_band_ann_pct": FLAT_BAND_ANN_PCT,
            "verification": ESTIMATED + " (slope computed from the verified strip below)",
        },
        "strip": strip,
        "contracts_returned": len(strip),
        "caveat": "Yahoo strip quotes are delayed and far months can be thinly traded — "
                  "structure (sign) is robust, the precise slope number is not.",
    })
    return out


def analyze(keys: list[str] | None = None) -> dict:
    return {k: analyze_one(k) for k in (keys or list(COMMODITIES))}


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    keys = sys.argv[1:] or ["wti", "gold", "copper"]
    print(json.dumps(analyze(keys), indent=2, default=str))
