"""
Part A smoke test — runs a real fetch against every commodity ingestor,
prints a console connection/setup report, and dumps each source's raw payload
to pipeline/commodities/data/smoke/<source>.json for inspection.

This is the gate before any analysis layer is built: a source that does not
produce verifiable raw output here does not get an agent built on top of it.

Usage:  python pipeline/commodities/smoke_test.py
"""

import json
import logging
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commodities.sources import cot, eia, macro, metals_supply, news, prices, usda  # noqa: E402
from config import EIA_API_KEY, FRED_API_KEY, USDA_NASS_API_KEY  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "data" / "smoke"

SOURCES = [
    ("prices", prices), ("eia", eia), ("usda", usda), ("cot", cot),
    ("macro", macro), ("metals_supply", metals_supply), ("news", news),
]

KEYS = [
    ("FRED_API_KEY", FRED_API_KEY, "already registered",
     "https://fredaccount.stlouisfed.org/apikeys"),
    ("EIA_API_KEY", EIA_API_KEY,
     "register: https://www.eia.gov/opendata/register.php -> email arrives instantly -> add EIA_API_KEY=... to .env.local",
     "https://www.eia.gov/opendata/register.php"),
    ("USDA_NASS_API_KEY", USDA_NASS_API_KEY,
     "register: https://quickstats.nass.usda.gov/api -> 'Request API key' -> instant -> add USDA_NASS_API_KEY=... to .env.local",
     "https://quickstats.nass.usda.gov/api"),
]


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("COMMODITIES PIPELINE — PART A CONNECTION REPORT")
    print("=" * 78)

    print("\n-- API keys ------------------------------------------------------------")
    for name, val, hint, _url in KEYS:
        status = "PRESENT" if val else "MISSING"
        print(f"  [{status:7}] {name}" + ("" if val else f"\n             -> {hint}"))
    print("  [no key ] CFTC COT (publicreporting.cftc.gov) — public, anonymous")
    print("  [no key ] World Bank, USDA WASDE locator, all RSS feeds")

    print("\n-- Sources -------------------------------------------------------------")
    reports = {}
    for name, mod in SOURCES:
        try:
            rep = mod.smoke()
        except Exception:  # noqa: BLE001 — one broken source must not kill the report
            rep = {"source": name, "status": "CRASHED",
                   "traceback": traceback.format_exc()}
        reports[name] = rep
        raw = rep.pop("raw", None)
        path = OUT_DIR / f"{name}.json"
        path.write_text(json.dumps({"summary": rep, "raw": raw}, indent=2, default=str),
                        encoding="utf-8")
        print(f"\n  {name.upper()}  ({rep.get('source', '')})")
        for k, v in rep.items():
            if k in ("source",):
                continue
            print(f"    {k}: {v}")
        print(f"    raw dump -> {path}")

    print("\n" + "=" * 78)
    print("Raw payloads in pipeline/commodities/data/smoke/ — review before Part B.")
    print("Standard: every figure VERIFIED from a live fetch or labeled UNVERIFIED.")
    print("=" * 78)


if __name__ == "__main__":
    main()
