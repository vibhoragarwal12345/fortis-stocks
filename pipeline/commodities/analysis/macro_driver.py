"""
Agent 6: macro driver — the per-commodity macro read, assembled
deterministically from sources/macro.py plus the news headline flow.

Each commodity gets the macro factors that actually drive it:
  all         broad dollar direction (inverse relationship — a rising USD is
              a headwind for USD-priced commodities)
  gold/silver 10y TIPS real yield (the dominant gold driver; silver tags
              along with an industrial kicker)
  copper      industrial production + global growth (copper as growth proxy)
  energy      CPI backdrop + geopolitical headline flow
  ags         dollar + weather/geopolitics headline flow

Direction reads come from verified 1-month changes in the FRED series; the
headline layer counts and quotes recent titles but never treats their claims
as data. No LLM here — this layer is deterministic so the narrative agent
has clean inputs to cite.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.registry import COMMODITIES  # noqa: E402
from commodities.sources import macro, news  # noqa: E402
from commodities.sources.common import ESTIMATED, UNVERIFIED, utcnow_iso  # noqa: E402

log = logging.getLogger("commodities.macro_driver")


def _factor(fred: dict, sid: str, relationship: str) -> dict | None:
    s = fred.get(sid)
    if not s or s.get("status") != "OK":
        return None
    direction = s.get("direction_1m")
    out = {
        "series": sid, "label": s["label"],
        "latest": s["latest"], "one_month_ago": s.get("one_month_ago"),
        "direction_1m": direction, "relationship": relationship,
    }
    if s.get("yoy_pct"):
        out["yoy_pct"] = s["yoy_pct"]
    return out


def _read_direction(factor: dict | None, inverse: bool) -> str | None:
    """headwind/tailwind/neutral from a factor's 1m direction."""
    if not factor or factor.get("direction_1m") in (None, "flat"):
        return "neutral" if factor else None
    rising = factor["direction_1m"] == "up"
    return ("headwind" if rising == inverse else "tailwind")


def analyze(keys: list[str] | None = None,
            macro_data: dict | None = None,
            news_data: dict | None = None) -> dict:
    keys = keys or list(COMMODITIES)
    if macro_data is None:
        macro_data = macro.fetch_all()
    if news_data is None:
        news_data = news.fetch_all()
    fred = macro_data.get("fred", {})
    wb = macro_data.get("world_bank", {}).get("global_gdp_growth", {})

    dollar = _factor(fred, "DTWEXBGS", "inverse — rising dollar pressures USD-priced commodities")
    real_rate = _factor(fred, "DFII10", "inverse for gold — rising real yields raise the cost of holding zero-yield bullion")
    cpi = _factor(fred, "CPIAUCSL", "supportive when elevated — commodities as the inflation expression")
    indpro = _factor(fred, "INDPRO", "direct for industrial metals — physical demand proxy")

    out = {}
    for k in keys:
        cat = COMMODITIES[k]["category"]
        factors: dict = {}
        if dollar:
            factors["dollar"] = {**dollar, "read": _read_direction(dollar, inverse=True)}
        if k in ("gold", "silver") and real_rate:
            factors["real_rates"] = {**real_rate, "read": _read_direction(real_rate, inverse=True)}
        if k in ("copper", "silver") and indpro:
            factors["industrial_demand"] = {**indpro,
                                            "read": _read_direction(indpro, inverse=False)}
        if cat == "energy" and cpi:
            factors["inflation_backdrop"] = {**cpi}
        if k == "copper" and wb.get("status") == "OK":
            factors["global_growth_baseline"] = wb

        heads = (news_data.get("per_commodity", {}).get(k, {}) or {})
        headlines = [h["title"] for h in (heads.get("headlines") or [])][:5]

        if not factors:
            out[k] = {"commodity": k, "computed_at": utcnow_iso(),
                      "status": "NO_MACRO_DATA", "verification": UNVERIFIED}
            continue
        reads = [v.get("read") for v in factors.values() if v.get("read")]
        net = ("headwind" if reads.count("headwind") > reads.count("tailwind")
               else "tailwind" if reads.count("tailwind") > reads.count("headwind")
               else "mixed")
        out[k] = {
            "commodity": k,
            "computed_at": utcnow_iso(),
            "status": "OK",
            "factors": factors,
            "net_macro_read": net,
            "net_macro_rule": "majority of factor headwind/tailwind reads; ties = mixed",
            "recent_headlines": {
                "titles": headlines,
                "caveat": "headline existence verified; claims inside are narrative, not data",
            },
            "verification": ESTIMATED + " (assembled from verified FRED/World Bank observations)",
        }
    return out


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    keys = sys.argv[1:] or ["wti", "gold", "copper"]
    print(json.dumps(analyze(keys), indent=2, default=str))
