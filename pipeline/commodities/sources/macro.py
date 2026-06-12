"""
Macro context for commodities — FRED + World Bank.

The handful of macro series that drive commodity pricing as an asset class:
  * Broad dollar index (DTWEXBGS) — the inverse-USD relationship is core
  * 10y TIPS real yield (DFII10) — gold's primary macro driver
  * CPI (CPIAUCSL) — inflation backdrop, computed YoY
  * Industrial production (INDPRO) — physical demand proxy (copper etc.)
  * 10y nominal (DGS10) — rates backdrop
  * World Bank: latest global real GDP growth — the demand baseline

Each series returns latest value + 1-month-ago + direction, all VERIFIED from
the live fetch; YoY/derived numbers are ESTIMATED from verified observations.

Usage:  python pipeline/commodities/sources/macro.py   # smoke test
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.sources.common import (  # noqa: E402
    ESTIMATED, UNVERIFIED, fred_observations, get_json, utcnow_iso, verified,
)
from config import FRED_API_KEY  # noqa: E402

log = logging.getLogger("commodities.macro")

FRED_SERIES = {
    "DTWEXBGS": {"label": "Broad US Dollar Index", "yoy": False},
    "DFII10":   {"label": "10Y TIPS Real Yield (%)", "yoy": False},
    "CPIAUCSL": {"label": "CPI (index)", "yoy": True},
    "INDPRO":   {"label": "Industrial Production (index)", "yoy": True},
    "DGS10":    {"label": "10Y Treasury Yield (%)", "yoy": False},
}

WORLDBANK_GDP_URL = ("https://api.worldbank.org/v2/country/WLD/indicator/"
                     "NY.GDP.MKTP.KD.ZG")


def _series_read(series_id: str, meta: dict) -> dict:
    obs = fred_observations(series_id, FRED_API_KEY, limit=400)
    if not obs:
        return {"status": "FETCH_FAILED", "label": meta["label"], "verification": UNVERIFIED}
    latest = obs[0]
    out = {
        "status": "OK",
        "label": meta["label"],
        "latest": {"value": latest["value"], "date": latest["date"],
                   "verification": verified(f"FRED {series_id}")},
    }
    # ~1 month ago: 21 obs back for daily series, 1 back for monthly.
    # Frequency detected from the gap between the two newest observations —
    # observation *count* is useless here because we fetch 400 for both.
    from datetime import date
    gap_days = 31
    if len(obs) > 1:
        d0, d1 = (date.fromisoformat(obs[0]["date"]), date.fromisoformat(obs[1]["date"]))
        gap_days = (d0 - d1).days
    idx_1m = 21 if gap_days <= 7 else 1
    if len(obs) > idx_1m:
        ago = obs[idx_1m]
        out["one_month_ago"] = {"value": ago["value"], "date": ago["date"],
                                "verification": verified(f"FRED {series_id}")}
        out["direction_1m"] = ("up" if latest["value"] > ago["value"]
                               else "down" if latest["value"] < ago["value"] else "flat")
    if meta["yoy"] and len(obs) >= 13:
        yr_ago = obs[12]  # monthly series
        if yr_ago["value"]:
            out["yoy_pct"] = {
                "value": round((latest["value"] - yr_ago["value"]) / yr_ago["value"] * 100, 2),
                "vs_date": yr_ago["date"],
                "verification": ESTIMATED + " (YoY computed from two verified FRED observations)",
            }
    return out


def _world_gdp() -> dict:
    # The World Bank API gateway 502s on mrnev/date filters and on
    # per_page > 5 (verified 2026-06-12) — only a small plain query is
    # reliable. Series is sorted newest-first; latest year is often null
    # until published, so pick the newest non-null of the top 5.
    data = get_json(WORLDBANK_GDP_URL, {"format": "json", "per_page": 5})
    try:
        rows = [r for r in data[1] if r.get("value") is not None]
        row = max(rows, key=lambda r: r["date"])
        return {
            "status": "OK",
            "label": "World real GDP growth (%, annual)",
            "value": round(float(row["value"]), 2),
            "year": row["date"],
            "verification": verified("World Bank NY.GDP.MKTP.KD.ZG"),
            "note": "Annual series — baseline context, lags ~1 year by nature.",
        }
    except (TypeError, KeyError, IndexError, ValueError):
        return {"status": "FETCH_FAILED", "verification": UNVERIFIED}


def fetch_all() -> dict:
    out: dict = {"fetched_at": utcnow_iso(), "fred": {}, "world_bank": {}}
    out["world_bank"]["global_gdp_growth"] = _world_gdp()  # no key needed
    if not FRED_API_KEY:
        out["status"] = "NO_KEY"
        out["note"] = "FRED_API_KEY missing — FRED macro layer UNVERIFIED (World Bank still fetched)."
        return out
    for sid, meta in FRED_SERIES.items():
        out["fred"][sid] = _series_read(sid, meta)
    ok = sum(1 for v in out["fred"].values() if v["status"] == "OK")
    out["status"] = "OK" if ok == len(FRED_SERIES) else ("PARTIAL" if ok else "ALL_FAILED")
    return out


def smoke() -> dict:
    data = fetch_all()
    return {
        "source": "macro (FRED + World Bank)",
        "needs_key": "FRED_API_KEY " + ("(present)" if FRED_API_KEY else "MISSING"),
        "status": data.get("status"),
        "fred_ok": [s for s, v in data.get("fred", {}).items() if v.get("status") == "OK"],
        "fred_failed": [s for s, v in data.get("fred", {}).items() if v.get("status") != "OK"],
        "world_bank": data.get("world_bank", {}).get("global_gdp_growth", {}).get("status"),
        "raw": data,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    print(json.dumps(smoke(), indent=2, default=str))
