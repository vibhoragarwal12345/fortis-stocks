"""
EIA Open Data API v2 — the inventory backbone for the energy complex.

Weekly series (refreshed Wed ~10:30am ET for petroleum, Thu for nat gas):
  crude stocks ex-SPR, total gasoline, distillate, SPR level, refinery
  utilization, and Lower-48 working natural gas in storage.

The key signal computed here: current level vs the 5-year average for the
same week of year (±2 weeks window) — labeled ESTIMATED because it is derived,
from VERIFIED weekly observations.

Uses the v2 `seriesid` compatibility route so each series is one stable URL:
    https://api.eia.gov/v2/seriesid/<SERIES>?api_key=...

Requires EIA_API_KEY (free, instant): https://www.eia.gov/opendata/register.php

Usage:  python pipeline/commodities/sources/eia.py   # smoke test
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.sources.common import (  # noqa: E402
    ESTIMATED, UNVERIFIED, get_json, utcnow_iso, verified,
)
from config import EIA_API_KEY  # noqa: E402

log = logging.getLogger("commodities.eia")

SERIESID_URL = "https://api.eia.gov/v2/seriesid/{series}"

# series id -> (label, unit, commodity keys it informs)
SERIES = {
    "PET.WCESTUS1.W": ("US crude oil stocks excl. SPR", "thousand barrels", ["wti", "brent"]),
    "PET.WCSSTUS1.W": ("US Strategic Petroleum Reserve", "thousand barrels", ["wti", "brent"]),
    "PET.WGTSTUS1.W": ("US total gasoline stocks", "thousand barrels", ["wti", "brent"]),
    "PET.WDISTUS1.W": ("US distillate stocks", "thousand barrels", ["wti", "brent"]),
    "PET.WPULEUS3.W": ("US refinery utilization", "percent of operable capacity", ["wti", "brent"]),
    "NG.NW2_EPG0_SWO_R48_BCF.W": ("Lower-48 working natural gas in storage", "Bcf", ["natgas"]),
}

WEEKS_HISTORY = 330  # ~6.3 years of weekly data: enough for a 5-year average


def _week_of_year(date_str: str) -> int:
    return datetime.strptime(date_str, "%Y-%m-%d").isocalendar()[1]


def _five_year_avg(rows: list[dict], latest_date: str) -> float | None:
    """Average of observations in the prior 5 years within ±2 weeks of the
    latest observation's week-of-year."""
    target_week = _week_of_year(latest_date)
    latest_year = int(latest_date[:4])
    vals = []
    for r in rows:
        y = int(r["period"][:4])
        if not (latest_year - 5 <= y <= latest_year - 1):
            continue
        wk = _week_of_year(r["period"])
        if min(abs(wk - target_week), 53 - abs(wk - target_week)) <= 2:
            vals.append(r["value"])
    return round(sum(vals) / len(vals), 1) if vals else None


def _fetch_series(series_id: str) -> list[dict] | None:
    """Most-recent-first [{period, value}] or None."""
    data = get_json(
        SERIESID_URL.format(series=series_id),
        {"api_key": EIA_API_KEY, "data[0]": "value",
         "sort[0][column]": "period", "sort[0][direction]": "desc",
         "length": WEEKS_HISTORY},
    )
    try:
        rows = data["response"]["data"]
    except (TypeError, KeyError):
        return None
    out = []
    for r in rows:
        try:
            out.append({"period": r["period"], "value": float(r["value"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


def fetch_all() -> dict:
    out: dict = {"fetched_at": utcnow_iso(), "series": {}}
    if not EIA_API_KEY:
        out["status"] = "NO_KEY"
        out["note"] = ("EIA_API_KEY missing — register free at "
                       "https://www.eia.gov/opendata/register.php and add to .env.local. "
                       "Entire energy inventory layer is UNVERIFIED until then.")
        return out

    ok = 0
    for sid, (label, unit, informs) in SERIES.items():
        rows = _fetch_series(sid)
        if not rows:
            out["series"][sid] = {"status": "FETCH_FAILED", "label": label,
                                  "verification": UNVERIFIED}
            continue
        latest, prev = rows[0], (rows[1] if len(rows) > 1 else None)
        avg5 = _five_year_avg(rows, latest["period"])
        entry = {
            "status": "OK",
            "label": label,
            "unit": unit,
            "informs": informs,
            "latest": {"period": latest["period"], "value": latest["value"],
                       "verification": verified(f"EIA {sid}")},
            "weekly_change": round(latest["value"] - prev["value"], 1) if prev else None,
            "history_weeks": len(rows),
        }
        if avg5 is not None:
            entry["five_year_avg_same_week"] = {
                "value": avg5,
                "vs_current_pct": round((latest["value"] - avg5) / avg5 * 100, 2) if avg5 else None,
                "verification": ESTIMATED + f" (5-yr same-week avg computed from {len(rows)} verified EIA weekly observations)",
            }
        out["series"][sid] = entry
        ok += 1
    out["status"] = "OK" if ok == len(SERIES) else ("PARTIAL" if ok else "ALL_FAILED")
    return out


def smoke() -> dict:
    data = fetch_all()
    return {
        "source": "EIA Open Data v2 (weekly energy inventories)",
        "needs_key": "EIA_API_KEY " + ("(present)" if EIA_API_KEY else "MISSING — free at eia.gov/opendata/register.php"),
        "status": data.get("status"),
        "series_ok": [s for s, v in data.get("series", {}).items() if v.get("status") == "OK"],
        "series_failed": [s for s, v in data.get("series", {}).items() if v.get("status") != "OK"],
        "raw": data,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    print(json.dumps(smoke(), indent=2, default=str))
