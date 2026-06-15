"""
Metals supply/demand layer — HONESTLY PARTIAL by design.

What is free and therefore VERIFIED here:
  * FRED monthly global price index (IMF) for copper. Low-frequency trend
    context. (Iron ore / steel was dropped 2026-06-15 — see registry.py.)

What is NOT freely available, and is therefore flagged, never faked:
  * LME real-time inventories (paid)                       -> UNVERIFIED
  * World Gold Council central-bank buying / ETF flows
    (registration-gated downloads, no stable free API)     -> UNVERIFIED
  * USGS Mineral Commodity Summaries: annual, published as PDFs/data releases
    with no stable machine API — treated as a manual-import slot, not fetched.

The analysis layer (Part B) must treat metals supply/demand as "thin data,
flagged" — it classifies tightening/loosening only from the verified price
trend + news, and says so.

Usage:  python pipeline/commodities/sources/metals_supply.py   # smoke test
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.sources.common import (  # noqa: E402
    ESTIMATED, UNVERIFIED, fred_observations, utcnow_iso, verified,
)
from config import FRED_API_KEY  # noqa: E402

log = logging.getLogger("commodities.metals_supply")

# series -> (label, commodity keys)
PRICE_TREND_SERIES = {
    "PCOPPUSDM":  ("Global copper price, USD/mt (IMF, monthly)", ["copper"]),
}

GATED_LAYERS = {
    "lme_inventories": {
        "status": "PAID_SOURCE",
        "verification": UNVERIFIED,
        "note": "LME warehouse stocks are licensed data — no free real-time feed. "
                "Metals inventory signal is absent, not estimated.",
        "informs": ["copper"],
    },
    "wgc_gold_flows": {
        "status": "GATED_SOURCE",
        "verification": UNVERIFIED,
        "note": "World Gold Council central-bank buying / ETF flow data sits behind "
                "registration downloads with no stable free API. Gold demand-flow "
                "signal comes from news headlines only, flagged as narrative.",
        "informs": ["gold"],
    },
    "usgs_mcs": {
        "status": "MANUAL_IMPORT_SLOT",
        "verification": UNVERIFIED,
        "note": "USGS Mineral Commodity Summaries are annual PDF/data releases with no "
                "stable machine API. If desired, drop the relevant CSVs into "
                "pipeline/commodities/data/usgs/ and a Part B parser can pick them up.",
        "informs": ["copper"],
    },
}


def _trend_read(series_id: str, label: str, informs: list[str]) -> dict:
    obs = fred_observations(series_id, FRED_API_KEY, limit=26)  # ~2 years monthly
    if not obs:
        return {"status": "FETCH_FAILED", "label": label, "verification": UNVERIFIED}
    latest = obs[0]
    out = {
        "status": "OK",
        "label": label,
        "informs": informs,
        "latest": {"value": latest["value"], "date": latest["date"],
                   "verification": verified(f"FRED {series_id}")},
        "frequency_warning": "Monthly series — trend context only, weeks stale by nature.",
    }
    for months, name in ((3, "chg_3m_pct"), (12, "chg_12m_pct")):
        if len(obs) > months and obs[months]["value"]:
            out[name] = {
                "value": round((latest["value"] - obs[months]["value"]) / obs[months]["value"] * 100, 2),
                "verification": ESTIMATED + " (computed from verified FRED observations)",
            }
    return out


def fetch_all() -> dict:
    out: dict = {"fetched_at": utcnow_iso(), "price_trends": {}, "gated_layers": GATED_LAYERS}
    if not FRED_API_KEY:
        out["status"] = "NO_KEY"
        return out
    ok = 0
    for sid, (label, informs) in PRICE_TREND_SERIES.items():
        read = _trend_read(sid, label, informs)
        out["price_trends"][sid] = read
        ok += read["status"] == "OK"
    out["status"] = "OK" if ok == len(PRICE_TREND_SERIES) else ("PARTIAL" if ok else "ALL_FAILED")
    out["honesty_note"] = ("Metals supply/demand depth is structurally limited on free data. "
                           "Verified: monthly global price trends. Unverified/absent: LME stocks, "
                           "WGC flows, USGS annual balances. Analysis must say so.")
    return out


def smoke() -> dict:
    data = fetch_all()
    return {
        "source": "metals supply (FRED trends + honestly-flagged gated layers)",
        "needs_key": "FRED_API_KEY " + ("(present)" if FRED_API_KEY else "MISSING"),
        "status": data.get("status"),
        "trends_ok": [s for s, v in data.get("price_trends", {}).items() if v.get("status") == "OK"],
        "gated_unverified": list(GATED_LAYERS.keys()),
        "raw": data,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    print(json.dumps(smoke(), indent=2, default=str))
