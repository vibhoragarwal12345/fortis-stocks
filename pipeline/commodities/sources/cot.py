"""
CFTC Commitments of Traders — the positioning / "smart money" layer.

Source: CFTC public reporting API (Socrata), legacy futures-only dataset
6dca-aqww at publicreporting.cftc.gov. Free, no key. Weekly: published Friday
~3:30pm ET with Tuesday's data — always label the report date, the data is
3+ days old by construction.

Per commodity:
  * commercial (hedger) and non-commercial (large speculator) net positions
  * week-over-week change in each
  * percentile of the current spec net position vs ~3 years of history —
    flags positioning extremes (>=90th or <=10th pct = crowding/reversal risk)

Brent and iron ore are not in CFTC data (ICE Europe / SGX) — honestly flagged.

Usage:  python pipeline/commodities/sources/cot.py   # smoke test
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.registry import COMMODITIES  # noqa: E402
from commodities.sources.common import (  # noqa: E402
    ESTIMATED, UNVERIFIED, get_json, utcnow_iso, verified,
)

log = logging.getLogger("commodities.cot")

SOCRATA_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
HISTORY_WEEKS = 160  # ~3 years for the percentile context

FIELDS = ",".join([
    "report_date_as_yyyy_mm_dd", "market_and_exchange_names",
    "cftc_contract_market_code",
    "noncomm_positions_long_all", "noncomm_positions_short_all",
    "comm_positions_long_all", "comm_positions_short_all",
    "open_interest_all",
])


def _query(where: str) -> list[dict] | None:
    return get_json(SOCRATA_URL, {
        "$select": FIELDS,
        "$where": where,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": HISTORY_WEEKS,
    })


def _rows_for(spec: dict) -> list[dict] | None:
    code = spec.get("cftc_code")
    if code:
        rows = _query(f"cftc_contract_market_code='{code}'")
        if rows:
            return rows
        log.warning("COT code %s returned nothing, trying name fallback", code)
    hint = spec.get("cftc_name_hint")
    if hint:
        return _query(f"upper(market_and_exchange_names) like '%{hint.upper()}%'")
    return None


def _net(row: dict, side: str) -> int | None:
    try:
        return int(float(row[f"{side}_positions_long_all"])) - int(float(row[f"{side}_positions_short_all"]))
    except (KeyError, TypeError, ValueError):
        return None


def _percentile(value: int, history: list[int]) -> float | None:
    if not history:
        return None
    below = sum(1 for h in history if h <= value)
    return round(below / len(history) * 100, 1)


def _analyze(rows: list[dict]) -> dict:
    # Name fallback can match several contracts — keep the highest-OI market.
    by_market: dict[str, list[dict]] = {}
    for r in rows:
        by_market.setdefault(r["market_and_exchange_names"], []).append(r)
    market = max(by_market, key=lambda m: float(by_market[m][0].get("open_interest_all") or 0))
    rows = by_market[market]

    latest, prev = rows[0], (rows[1] if len(rows) > 1 else None)
    spec_net = _net(latest, "noncomm")
    comm_net = _net(latest, "comm")
    spec_hist = [n for r in rows if (n := _net(r, "noncomm")) is not None]
    pct = _percentile(spec_net, spec_hist) if spec_net is not None else None

    out = {
        "status": "OK",
        "market": market,
        "report_date": latest["report_date_as_yyyy_mm_dd"][:10],
        "data_lag_note": "COT data is as-of Tuesday, released Friday — minimum 3 days stale",
        "open_interest": int(float(latest.get("open_interest_all") or 0)),
        "noncommercial_net": spec_net,
        "commercial_net": comm_net,
        "noncommercial_net_wow_change": (spec_net - _net(prev, "noncomm"))
            if prev and spec_net is not None and _net(prev, "noncomm") is not None else None,
        "commercial_net_wow_change": (comm_net - _net(prev, "comm"))
            if prev and comm_net is not None and _net(prev, "comm") is not None else None,
        "verification": verified("CFTC publicreporting.cftc.gov 6dca-aqww"),
    }
    if pct is not None:
        out["spec_net_percentile_3y"] = {
            "value": pct,
            "weeks_of_history": len(spec_hist),
            "extreme": pct >= 90 or pct <= 10,
            "verification": ESTIMATED + f" (percentile computed from {len(spec_hist)} verified weekly reports)",
        }
    return out


def fetch_all() -> dict:
    out: dict = {"fetched_at": utcnow_iso(), "commodities": {}}
    ok = 0
    for key, spec in COMMODITIES.items():
        if not spec.get("cftc_code") and not spec.get("cftc_name_hint"):
            out["commodities"][key] = {
                "status": "NOT_IN_CFTC_DATA",
                "verification": UNVERIFIED,
                "note": "Contract trades outside CFTC jurisdiction (ICE Europe / SGX / DCE); "
                        "no free positioning data — layer absent, not estimated.",
            }
            continue
        rows = _rows_for(spec)
        if not rows:
            out["commodities"][key] = {"status": "FETCH_FAILED", "verification": UNVERIFIED}
            continue
        try:
            out["commodities"][key] = _analyze(rows)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("COT analyze failed for %s: %s", key, exc)
            out["commodities"][key] = {"status": "PARSE_FAILED", "verification": UNVERIFIED}
    out["status"] = "OK" if ok else "ALL_FAILED"
    return out


def smoke() -> dict:
    data = fetch_all()
    return {
        "source": "CFTC COT legacy futures-only (Socrata, no key)",
        "needs_key": "none",
        "status": data["status"],
        "ok": [k for k, v in data["commodities"].items() if v.get("status") == "OK"],
        "unavailable": [k for k, v in data["commodities"].items() if v.get("status") != "OK"],
        "raw": data,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    print(json.dumps(smoke(), indent=2, default=str))
