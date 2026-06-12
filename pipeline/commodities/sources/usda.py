"""
USDA ag fundamentals — two free sources:

1. NASS Quick Stats API (key required, free):
   national production + quarterly grain stocks for corn / soybeans / wheat.
   Coffee is NOT covered here (NASS only has HI/PR micro-production) — the
   coffee supply layer is flagged LIMITED by design.

2. WASDE latest-release locator (no key): the monthly WASDE report is
   published through Cornell's usda.library.cornell.edu API. Part A only
   locates and verifies the latest release (date + file links); parsing the
   balance sheet into stocks-to-use comes in the analysis layer (Part B).

Requires USDA_NASS_API_KEY (free, instant): https://quickstats.nass.usda.gov/api

Usage:  python pipeline/commodities/sources/usda.py   # smoke test
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.registry import COMMODITIES  # noqa: E402
from commodities.sources.common import (  # noqa: E402
    UNVERIFIED, get_json, utcnow_iso, verified,
)
from config import USDA_NASS_API_KEY  # noqa: E402

log = logging.getLogger("commodities.usda")

NASS_URL = "https://quickstats.nass.usda.gov/api/api_GET/"
WASDE_RELEASE_URL = "https://usda.library.cornell.edu/api/v1/release/findByIdentifier/wasde"


def _nass_query(params: dict) -> list[dict] | None:
    base = {"key": USDA_NASS_API_KEY, "format": "JSON",
            "agg_level_desc": "NATIONAL", "source_desc": "SURVEY"}
    data = get_json(NASS_URL, {**base, **params})
    if not data or "data" not in data:
        return None
    return data["data"] or None


def _latest_stat(commodity: str, statisticcat: str, short_desc_contains: str) -> dict | None:
    """Most recent national observation for a stat category, filtered to the
    headline series (e.g. production in BU, not silage in tons)."""
    year_floor = datetime.now().year - 2
    rows = _nass_query({
        "commodity_desc": commodity,
        "statisticcat_desc": statisticcat,
        "year__GE": str(year_floor),
    })
    if not rows:
        return None
    rows = [r for r in rows if short_desc_contains in r.get("short_desc", "")]
    if not rows:
        return None
    # sort: newest year, then latest reference period within the year
    rows.sort(key=lambda r: (int(r.get("year", 0)), r.get("end_code") or "00",
                             r.get("reference_period_desc", "")), reverse=True)
    r = rows[0]
    raw_val = (r.get("Value") or "").replace(",", "").strip()
    try:
        value = float(raw_val)
    except ValueError:
        return None
    return {
        "value": value,
        "unit": r.get("unit_desc"),
        "year": r.get("year"),
        "period": r.get("reference_period_desc"),
        "short_desc": r.get("short_desc"),
        "verification": verified("USDA NASS Quick Stats"),
    }


def fetch_nass() -> dict:
    out: dict = {"fetched_at": utcnow_iso(), "commodities": {}}
    if not USDA_NASS_API_KEY:
        out["status"] = "NO_KEY"
        out["note"] = ("USDA_NASS_API_KEY missing — register free at "
                       "https://quickstats.nass.usda.gov/api and add to .env.local. "
                       "Ag fundamentals layer is UNVERIFIED until then.")
        return out

    ok = 0
    for key, spec in COMMODITIES.items():
        nass = spec.get("nass_commodity")
        if spec["category"] != "ags":
            continue
        if not nass:
            out["commodities"][key] = {
                "status": "LIMITED_COVERAGE",
                "verification": UNVERIFIED,
                "note": "NASS has no meaningful national series for this commodity "
                        "(coffee: HI/PR only). Supply layer = news + price only.",
            }
            continue
        prod = _latest_stat(nass, "PRODUCTION", "PRODUCTION, MEASURED IN BU")
        stocks = _latest_stat(nass, "STOCKS", "STOCKS, MEASURED IN BU")
        yield_ = _latest_stat(nass, "YIELD", "YIELD, MEASURED IN BU")
        entry = {"status": "OK" if (prod or stocks) else "FETCH_FAILED",
                 "production": prod, "grain_stocks": stocks, "yield": yield_}
        if not (prod or stocks):
            entry["verification"] = UNVERIFIED
        out["commodities"][key] = entry
        ok += 1 if (prod or stocks) else 0
    out["status"] = "OK" if ok else "ALL_FAILED"
    return out


def fetch_wasde_release() -> dict:
    """Locate the latest WASDE release (metadata + file URLs). No key needed."""
    data = get_json(WASDE_RELEASE_URL, {"latest": "true"})
    if not data:
        return {"status": "FETCH_FAILED", "verification": UNVERIFIED,
                "note": "Could not reach usda.library.cornell.edu release API."}
    try:
        results = data.get("results") if isinstance(data, dict) else data
        if not results:
            return {"status": "EMPTY_RESPONSE", "verification": UNVERIFIED}
        rel = results[0]
        return {
            "status": "OK",
            "title": rel.get("title"),
            "release_date": rel.get("release_datetime"),
            "files": rel.get("files", [])[:6],
            "verification": verified("usda.library.cornell.edu WASDE release API"),
            "note": "Release located and verified; balance-sheet parsing (stocks-to-use) is a Part B analysis step.",
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("WASDE metadata parse failed: %s", exc)
        return {"status": "PARSE_FAILED", "verification": UNVERIFIED}


def fetch_all() -> dict:
    return {"nass": fetch_nass(), "wasde": fetch_wasde_release()}


def smoke() -> dict:
    data = fetch_all()
    nass = data["nass"]
    return {
        "source": "USDA (NASS Quick Stats + WASDE release locator)",
        "needs_key": "USDA_NASS_API_KEY " + ("(present)" if USDA_NASS_API_KEY else "MISSING — free at quickstats.nass.usda.gov/api"),
        "nass_status": nass.get("status"),
        "nass_ok": [k for k, v in nass.get("commodities", {}).items() if v.get("status") == "OK"],
        "wasde_status": data["wasde"].get("status"),
        "raw": data,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    print(json.dumps(smoke(), indent=2, default=str))
