"""
Agent 2: supply/demand — the commodity equivalent of fundamentals.
Classifies each commodity as TIGHTENING / BALANCED / LOOSENING, with the
evidence and its verification labels attached, and an explicit confidence
that reflects how much real data the classification rests on.

Per category:
  energy  EIA inventories vs 5-yr same-week average + refinery utilization
          (crude) or storage vs 5-yr (natgas). Richest data -> high confidence.
  ags     NASS stocks YoY change + stocks/production ratio (a stocks-to-use
          PROXY — true stocks-to-use needs the WASDE total-use line, which is
          a planned parse of the located WASDE XML; labeled accordingly).
  metals  price-inferred only (3m/12m FRED trend) — supply data is gated
          (LME paid, WGC gated, USGS annual) -> LOW confidence, said openly.

Classification thresholds are conventions, not magic: ±3% vs the 5-yr
average for energy inventories, ±5% YoY for ag stocks, ±8%/3m for
price-inferred metals. They are stated in the output so the narrative layer
can cite them honestly.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.registry import COMMODITIES  # noqa: E402
from commodities.sources import eia, metals_supply, usda  # noqa: E402
from commodities.sources.common import ESTIMATED, UNVERIFIED, utcnow_iso  # noqa: E402

log = logging.getLogger("commodities.supply_demand")

ENERGY_THRESHOLD_PCT = 3.0
AG_YOY_THRESHOLD_PCT = 5.0
METALS_3M_THRESHOLD_PCT = 8.0


def _classify(value: float, threshold: float, inverted: bool = False) -> str:
    """Inventories/stocks BELOW norm = tightening. `inverted` flips the sign
    convention for price-based reads (price UP = tightening)."""
    sign = -1 if inverted else 1
    if value * sign <= -threshold:
        return "tightening"
    if value * sign >= threshold:
        return "loosening"
    return "balanced"


def _energy_read(key: str, eia_data: dict) -> dict:
    series = eia_data.get("series", {})
    if eia_data.get("status") in ("NO_KEY", "ALL_FAILED", None):
        return {"classification": "unknown", "confidence": "none",
                "verification": UNVERIFIED, "note": "EIA layer unavailable."}

    if key == "natgas":
        s = series.get("NG.NW2_EPG0_SWO_R48_BCF.W", {})
        vs5 = s.get("five_year_avg_same_week", {}).get("vs_current_pct")
        if vs5 is None:
            return {"classification": "unknown", "confidence": "none", "verification": UNVERIFIED}
        return {
            "classification": _classify(vs5, ENERGY_THRESHOLD_PCT),
            "confidence": "high",
            "evidence": {
                "storage_bcf": s.get("latest"),
                "storage_vs_5yr_pct": vs5,
                "weekly_injection_bcf": s.get("weekly_change"),
            },
            "rule": f"storage vs 5-yr same-week avg, ±{ENERGY_THRESHOLD_PCT}% threshold",
            "verification": ESTIMATED + " (classified from verified EIA weekly data)",
        }

    crude = series.get("PET.WCESTUS1.W", {})
    util = series.get("PET.WPULEUS3.W", {})
    dist = series.get("PET.WDISTUS1.W", {})
    spr = series.get("PET.WCSSTUS1.W", {})
    vs5 = crude.get("five_year_avg_same_week", {}).get("vs_current_pct")
    if vs5 is None:
        return {"classification": "unknown", "confidence": "none", "verification": UNVERIFIED}
    return {
        "classification": _classify(vs5, ENERGY_THRESHOLD_PCT),
        "confidence": "high",
        "evidence": {
            "crude_stocks": crude.get("latest"),
            "crude_vs_5yr_pct": vs5,
            "crude_weekly_change": crude.get("weekly_change"),
            "refinery_utilization_pct": (util.get("latest") or {}).get("value"),
            "distillate_vs_5yr_pct": dist.get("five_year_avg_same_week", {}).get("vs_current_pct"),
            "spr_vs_5yr_pct": spr.get("five_year_avg_same_week", {}).get("vs_current_pct"),
        },
        "rule": f"crude stocks ex-SPR vs 5-yr same-week avg, ±{ENERGY_THRESHOLD_PCT}% threshold",
        "note": "US-centric: EIA covers US balances; global (OPEC/IEA) balances are not in the free layer."
                + (" Same read applied to Brent via the shared US data — flagged as a proxy."
                   if key == "brent" else ""),
        "verification": ESTIMATED + " (classified from verified EIA weekly data)",
    }


def _ag_read(key: str, nass_data: dict, wasde: dict) -> dict:
    c = nass_data.get("commodities", {}).get(key, {})
    if c.get("status") == "LIMITED_COVERAGE":
        return {"classification": "unknown", "confidence": "none",
                "verification": UNVERIFIED,
                "note": "No NASS coverage (coffee) — supply read is news + price only, by design."}
    if c.get("status") != "OK":
        return {"classification": "unknown", "confidence": "none", "verification": UNVERIFIED}

    stocks = c.get("grain_stocks") or {}
    prior = c.get("grain_stocks_prior_year") or {}
    prod = c.get("production") or {}
    evidence: dict = {"grain_stocks": stocks, "grain_stocks_prior_year": prior or None,
                      "production": prod}

    stocks_yoy = None
    if stocks.get("value") and prior.get("value"):
        stocks_yoy = round((stocks["value"] - prior["value"]) / prior["value"] * 100, 2)
        evidence["stocks_yoy_pct"] = stocks_yoy
    if stocks.get("value") and prod.get("value"):
        evidence["stocks_to_production_proxy_pct"] = round(stocks["value"] / prod["value"] * 100, 1)
        evidence["proxy_caveat"] = ("stocks/production is a PROXY — true stocks-to-use needs the "
                                    "WASDE total-use line (parse pending); do not quote as stocks-to-use")

    if stocks_yoy is None:
        return {"classification": "unknown", "confidence": "low", "evidence": evidence,
                "verification": UNVERIFIED,
                "note": "Prior-year stocks unavailable — no YoY basis to classify."}
    return {
        "classification": _classify(stocks_yoy, AG_YOY_THRESHOLD_PCT),
        "confidence": "medium",
        "evidence": evidence,
        "rule": f"grain stocks YoY (same reference period), ±{AG_YOY_THRESHOLD_PCT}% threshold",
        "wasde_latest": {"release_date": wasde.get("release_date"),
                         "status": wasde.get("status"),
                         "note": "balance-sheet parse pending — located, not yet ingested"},
        "verification": ESTIMATED + " (classified from verified NASS observations)",
    }


def _metals_read(key: str, metals_data: dict) -> dict:
    trends = metals_data.get("price_trends", {})
    relevant = [v for v in trends.values()
                if v.get("status") == "OK" and key in v.get("informs", [])]
    gated = [g for g, v in metals_data.get("gated_layers", {}).items()
             if key in v.get("informs", [])]
    base_note = ("PRICE-INFERRED ONLY: real supply/demand data is gated "
                 f"(unavailable free layers: {', '.join(gated) if gated else 'none'}). "
                 "A price trend is weak evidence of balance — low confidence, by construction.")
    if not relevant:
        return {"classification": "unknown", "confidence": "none",
                "verification": UNVERIFIED, "note": base_note}
    primary = relevant[0]
    chg3 = (primary.get("chg_3m_pct") or {}).get("value")
    if chg3 is None:
        return {"classification": "unknown", "confidence": "none",
                "verification": UNVERIFIED, "note": base_note}
    return {
        "classification": _classify(chg3, METALS_3M_THRESHOLD_PCT, inverted=True),
        "confidence": "low",
        "evidence": {
            "primary_series": primary.get("label"),
            "latest": primary.get("latest"),
            "chg_3m_pct": chg3,
            "chg_12m_pct": (primary.get("chg_12m_pct") or {}).get("value"),
        },
        "rule": f"monthly global price 3m change, ±{METALS_3M_THRESHOLD_PCT}% threshold (price up = tightening)",
        "note": base_note,
        "verification": ESTIMATED + " (classified from verified FRED monthly observations)",
    }


def analyze(keys: list[str] | None = None,
            eia_data: dict | None = None,
            usda_data: dict | None = None,
            metals_data: dict | None = None) -> dict:
    """Source payloads can be injected (the scan fetches once and shares);
    when absent each is fetched here."""
    keys = keys or list(COMMODITIES)
    cats = {COMMODITIES[k]["category"] for k in keys}
    if eia_data is None and "energy" in cats:
        eia_data = eia.fetch_all()
    if usda_data is None and "ags" in cats:
        usda_data = usda.fetch_all()
    if metals_data is None and "metals" in cats:
        metals_data = metals_supply.fetch_all()

    out = {}
    for k in keys:
        cat = COMMODITIES[k]["category"]
        if cat == "energy":
            read = _energy_read(k, eia_data or {})
        elif cat == "ags":
            read = _ag_read(k, (usda_data or {}).get("nass", {}), (usda_data or {}).get("wasde", {}))
        else:
            read = _metals_read(k, metals_data or {})
        # Gold is a special metals case: its "supply/demand" is dominated by
        # investment/CB demand, which sits in the gated WGC layer.
        if k == "gold":
            read["note"] = ("Gold balance is driven by investment + central-bank demand "
                            "(gated WGC data) — price-inferred read is even weaker here; "
                            "macro_driver (real rates, dollar) is the better lens. "
                            + read.get("note", ""))
        out[k] = {"commodity": k, "category": cat, "computed_at": utcnow_iso(), **read}
    return out


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    keys = sys.argv[1:] or ["wti", "gold", "copper"]
    print(json.dumps(analyze(keys), indent=2, default=str))
