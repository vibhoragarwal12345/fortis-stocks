"""
Scan price reference bands -- zero-drift Monte Carlo cone per shortlist name.
=============================================================================

Honest statistical reference levels (NOT forecasts). We reuse the Quant Desk's
`MonteCarloSimulator` (calibrate a Normal/Student-t to the log returns, simulate
paths, summarize percentiles) but ZERO the drift -- exactly the discipline the
commodities desk landed on: empirical drift turns a trailing rally into a
"median +X%" band, i.e. momentum dressed up as statistics. Direction lives in
the bull/bear thesis; the band only describes volatility.

Deterministic (numpy/scipy) -- no LLM. Runs inside the scan, right after Layer 2,
so EVERY displayed name carries a price band regardless of the LLM budget (the
old LLM `price_target_*` only reached ~half the names). The dossier gate then
requires `price_reference` for a name to display.

Writes ranked_focus_list.price_reference (jsonb) -- see migration 050.

CLI
  python -m pipeline.scan.price_bands --scan-id 123
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from quant.monte_carlo import MonteCarloSimulator  # noqa: E402
from supabase import create_client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HORIZON_DAYS = 21       # ~1 trading month
N_SIMS = 20_000         # plenty for stable 5th/95th percentiles
MIN_HISTORY = 60        # need a decent series to calibrate honestly


def _band(prices: pd.Series) -> dict | None:
    """Zero-drift Monte Carlo reference cone for one price series."""
    prices = prices.dropna()
    if len(prices) < MIN_HISTORY:
        return None
    sim = MonteCarloSimulator()
    try:
        sim.calibrate(prices)
    except Exception as exc:  # noqa: BLE001
        log.debug("calibrate failed: %s", exc)
        return None
    # Zero the drift -- a reference cone, not a momentum forecast.
    if sim.distribution_type == "normal":
        sim.params["mu"] = 0.0
    else:
        sim.params["loc"] = 0.0
    start = float(prices.iloc[-1])
    try:
        paths = sim.simulate(start, HORIZON_DAYS, n_simulations=N_SIMS)
        s = sim.summary(paths)
    except Exception as exc:  # noqa: BLE001
        log.debug("simulate failed: %s", exc)
        return None
    return {
        "low":          s["p5_price"],
        "mid":          s["median_price"],
        "high":         s["p95_price"],
        "basis":        round(start, 4),
        "horizon_days": HORIZON_DAYS,
        "method":       "monte_carlo_zero_drift",
        "distribution": sim.distribution_type,
        "calibration":  sim.calibration_quality,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _download(tickers: list[str]) -> dict[str, pd.Series]:
    """{ticker: close-price Series} from 1y of daily data."""
    try:
        raw = yf.download(tickers, period="1y", interval="1d", auto_adjust=True,
                          progress=False, group_by="ticker")
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance download failed: %s", exc)
        return {}
    out: dict[str, pd.Series] = {}
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in tickers:
        try:
            df = (raw[t] if multi else raw).dropna(how="all").rename(columns=str.lower)
            if "close" in df:
                out[t] = df["close"]
        except (KeyError, IndexError, TypeError):
            continue
    return out


def run(scan_id: int) -> tuple[int, int]:
    """Compute + persist a price_reference cone for every pick in `scan_id`.

    Returns (with_band, total)."""
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    rows = (db.table("ranked_focus_list")
            .select("ticker")
            .eq("scan_id", scan_id)
            .order("rank").execute().data or [])
    tickers = [r["ticker"] for r in rows]
    if not tickers:
        log.warning("price_bands: no ranked_focus_list rows for scan_id=%d", scan_id)
        return 0, 0

    series = _download(tickers)

    # Inverse-volatility relative sizing (2026-07-15, absorbed from
    # Vibe-Trading's equal-volatility optimizer): suggested size relative to
    # an equal-weight slot, = list-median 60d vol / own vol, clipped to
    # [0.5, 1.5]. Math instead of LLM prose for position_size_guidance —
    # quieter names earn a fuller slot, wilder names a smaller one.
    vols: dict[str, float] = {}
    for t, s in series.items():
        r = s.dropna().pct_change().iloc[-60:]
        if len(r) >= 30 and float(r.std()) > 0:
            vols[t] = float(r.std())
    med_vol = float(pd.Series(vols).median()) if vols else None

    from scan.patterns import support_resistance, trend_slope_20d

    done = 0
    for t in tickers:
        band = _band(series[t]) if t in series else None
        if band is None:
            log.info("price_bands %s: no band (insufficient history)", t)
            continue

        # Chart structure (deterministic, from scan/patterns.py): where the
        # tape has historically stalled, alongside the cone's how-far.
        sr = support_resistance(series[t])
        if sr:
            band["support_resistance"] = sr
        slope = trend_slope_20d(series[t])
        if slope is not None:
            band["trend_slope_20d_pct"] = slope
        if med_vol and t in vols:
            band["suggested_relative_size"] = round(
                float(min(1.5, max(0.5, med_vol / vols[t]))), 2)
            band["sizing_basis"] = "inverse_volatility_vs_list_median_60d"
        try:
            (db.table("ranked_focus_list")
               .update({"price_reference": band})
               .eq("scan_id", scan_id).eq("ticker", t).execute())
            done += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("price_bands %s: persist failed: %s", t, exc)

    log.info("price_bands scan_id=%d: %d/%d picks carry a reference cone",
             scan_id, done, len(tickers))
    return done, len(tickers)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-id", type=int, required=True)
    args = ap.parse_args()
    run(args.scan_id)
    return 0  # a missing band never fails the scan


if __name__ == "__main__":
    sys.exit(main())
