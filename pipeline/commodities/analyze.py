"""
Commodity analysis orchestrator — runs the full Part B model.

Flow:
  1. fetch each shared source ONCE (eia/usda/cot/macro/metals/news)
  2. run agents 1-6 per commodity (technicals, supply/demand, curve,
     positioning, seasonality, macro driver)
  3. build TWO DISJOINT flat data subsets per commodity — tactical vs
     structural — and hand them to the narrative agent separately
  4. assemble the final read with the dual-horizon split at the TOP level
     of the output schema, each block carrying its own audience warning.

The horizon separation is architectural: a consumer rendering the
`structural` block never touches tactical fields because they are not in it.

Usage:
    python pipeline/commodities/analyze.py                # all commodities
    python pipeline/commodities/analyze.py wti gold copper
    python pipeline/commodities/analyze.py --no-llm wti   # skip narratives
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# NB: this package is commodities/analysis, NOT commodities/agents — a
# subpackage named `agents` would shadow the stock engine's top-level
# namespace package `agents` whenever a script runs from commodities/.
from commodities.analysis import (  # noqa: E402
    curve_structure, macro_driver, positioning, price_technicals,
    seasonality, supply_demand,
)
from commodities.analysis import narrative as narrative_agent  # noqa: E402
from commodities.registry import COMMODITIES  # noqa: E402
from commodities.sources import cot, eia, macro, metals_supply, news, usda  # noqa: E402
from commodities.sources.common import utcnow_iso  # noqa: E402

log = logging.getLogger("commodities.analyze")

DISCLAIMER = (
    "RESEARCH / INTELLIGENCE ONLY — NOT INVESTMENT ADVICE. Commodities carry "
    "higher risk than equities (leverage, volatility, geopolitical shocks). "
    "All reference levels are statistical, not forecasts. Data is delayed and "
    "partially incomplete (gaps are labeled, never filled). A qualified human "
    "must review any decision made downstream of this output."
)

TACTICAL_WARNING = (
    "SHORT-TERM / TACTICAL — about a one-week horizon (refreshed weekly), "
    "written for traders. "
    "NOT an investment thesis: acting on this with long-horizon capital "
    "is the overtrading failure mode this system is designed to prevent."
)
STRUCTURAL_WARNING = (
    "STRUCTURAL / LONG-TERM — multi-quarter horizon, written for investors. "
    "Carries no entry/exit timing information: do not trade off this read."
)


def _g(d: dict, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return d if d is not None else default


def _tactical_data(key: str, tech: dict, curve: dict, pos: dict,
                   season: dict, sd: dict) -> dict:
    """Flat, citable key -> value subset for the TACTICAL narrative."""
    d = {
        "commodity_name": COMMODITIES[key]["name"],
        "price_close": _g(tech, "price", "close"),
        "price_asof": _g(tech, "price", "asof"),
        "rsi_14": _g(tech, "momentum", "rsi_14"),
        "above_50dma": _g(tech, "trend", "above_sma50"),
        "atr_14": _g(tech, "volatility", "atr_14"),
        "realized_vol_30d_ann_pct": _g(tech, "volatility", "realized_vol_30d_ann_pct"),
        "pct_of_52w_range": _g(tech, "range_52w", "position_pct"),
        "nearest_support": _g(tech, "support_resistance", "nearest_support"),
        "nearest_resistance": _g(tech, "support_resistance", "nearest_resistance"),
        "band_1w_bear_p5": _g(tech, "reference_bands", "tactical_1w", "bear_p5"),
        "band_1w_normal_p50": _g(tech, "reference_bands", "tactical_1w", "normal_p50"),
        "band_1w_bull_p95": _g(tech, "reference_bands", "tactical_1w", "bull_p95"),
        "curve_structure": curve.get("structure"),
        "curve_annualized_slope_pct": _g(curve, "slope", "annualized_slope_pct"),
        "spec_positioning_stance": pos.get("spec_stance"),
        "spec_positioning_percentile_3y": pos.get("spec_net_percentile_3y"),
        "positioning_crowding": pos.get("crowding"),
        "positioning_weekly_flow": _g(pos, "weekly_flow", "summary"),
        "cot_report_date": pos.get("report_date"),
        "seasonal_tendency_this_month": _g(season, "current_month", "tendency"),
        "seasonal_avg_return_this_month_pct": _g(season, "current_month", "avg_return_pct"),
        "seasonal_hit_rate_this_month_pct": _g(season, "current_month", "hit_rate_pct"),
    }
    # the latest weekly inventory move is tactical (the surprise), the level
    # vs 5yr is structural — split accordingly. Units live in the key name:
    # the narrative LLM cites keys verbatim, so a bare "inventory_weekly_change"
    # produced unit-less numbers in published text.
    ev = sd.get("evidence", {})
    if COMMODITIES[key]["category"] == "energy":
        if ev.get("crude_weekly_change") is not None:
            d["crude_inventory_weekly_change_thousand_bbl"] = ev["crude_weekly_change"]
        if ev.get("weekly_injection_bcf") is not None:
            d["natgas_storage_weekly_injection_bcf"] = ev["weekly_injection_bcf"]
    return {k: v for k, v in d.items() if v is not None}


def _structural_data(key: str, tech: dict, curve: dict, sd: dict,
                     mac: dict) -> dict:
    """Flat, citable key -> value subset for the STRUCTURAL narrative."""
    ev = sd.get("evidence", {})
    d = {
        "commodity_name": COMMODITIES[key]["name"],
        "price_close": _g(tech, "price", "close"),
        "price_asof": _g(tech, "price", "asof"),
        "above_200dma": _g(tech, "trend", "above_sma200"),
        "pct_of_52w_range": _g(tech, "range_52w", "position_pct"),
        "band_1y_bear_p5": _g(tech, "reference_bands", "structural_252d", "bear_p5"),
        "band_1y_normal_p50": _g(tech, "reference_bands", "structural_252d", "normal_p50"),
        "band_1y_bull_p95": _g(tech, "reference_bands", "structural_252d", "bull_p95"),
        "supply_demand_classification": sd.get("classification"),
        "supply_demand_confidence": sd.get("confidence"),
        "supply_demand_data_caveat": sd.get("note"),
        "curve_structure": curve.get("structure"),
        "crude_inventories_vs_5yr_avg_pct": ev.get("crude_vs_5yr_pct"),
        "distillate_vs_5yr_avg_pct": ev.get("distillate_vs_5yr_pct"),
        "spr_vs_5yr_avg_pct": ev.get("spr_vs_5yr_pct"),
        "refinery_utilization_pct": ev.get("refinery_utilization_pct"),
        "natgas_storage_vs_5yr_avg_pct": ev.get("storage_vs_5yr_pct"),
        "grain_stocks_yoy_pct": ev.get("stocks_yoy_pct"),
        "stocks_to_production_proxy_pct": ev.get("stocks_to_production_proxy_pct"),
        "metals_price_chg_3m_pct": ev.get("chg_3m_pct"),
        "metals_price_chg_12m_pct": ev.get("chg_12m_pct"),
        "dollar_1m_direction": _g(mac, "factors", "dollar", "direction_1m"),
        "dollar_read": _g(mac, "factors", "dollar", "read"),
        "real_10y_tips_yield_pct": _g(mac, "factors", "real_rates", "latest", "value"),
        "real_rates_read": _g(mac, "factors", "real_rates", "read"),
        "industrial_production_yoy_pct": _g(mac, "factors", "industrial_demand", "yoy_pct", "value"),
        "global_gdp_growth_pct": _g(mac, "factors", "global_growth_baseline", "value"),
        "global_gdp_growth_year": _g(mac, "factors", "global_growth_baseline", "year"),
        "cpi_yoy_pct": _g(mac, "factors", "inflation_backdrop", "yoy_pct", "value"),
        "net_macro_read": mac.get("net_macro_read"),
    }
    return {k: v for k, v in d.items() if v is not None}


def run(keys: list[str] | None = None, with_llm: bool = True) -> dict:
    keys = keys or list(COMMODITIES)
    log.info("fetching shared sources once for %d commodities ...", len(keys))
    cats = {COMMODITIES[k]["category"] for k in keys}
    eia_data = eia.fetch_all() if "energy" in cats else {}
    usda_data = usda.fetch_all() if "ags" in cats else {}
    metals_data = metals_supply.fetch_all() if "metals" in cats else {}
    cot_data = cot.fetch_all()
    macro_data = macro.fetch_all()
    news_data = news.fetch_all()

    log.info("running analysis agents ...")
    tech_all = price_technicals.analyze(keys)
    sd_all = supply_demand.analyze(keys, eia_data=eia_data, usda_data=usda_data,
                                   metals_data=metals_data)
    curve_all = curve_structure.analyze(keys)
    pos_all = positioning.analyze(keys, cot_data=cot_data)
    season_all = seasonality.analyze(keys)
    mac_all = macro_driver.analyze(keys, macro_data=macro_data, news_data=news_data)

    out = {"generated_at": utcnow_iso(), "disclaimer": DISCLAIMER, "commodities": {}}
    for k in keys:
        tech, sd = tech_all[k], sd_all[k]
        curve, pos = curve_all[k], pos_all[k]
        season, mac = season_all[k], mac_all[k]

        tac_data = _tactical_data(k, tech, curve, pos, season, sd)
        struct_data = _structural_data(k, tech, curve, sd, mac)

        reads = (narrative_agent.generate_reads(k, tac_data, struct_data)
                 if with_llm else
                 {"tactical": {"status": "SKIPPED"}, "structural": {"status": "SKIPPED"}})

        out["commodities"][k] = {
            "name": COMMODITIES[k]["name"],
            "category": COMMODITIES[k]["category"],
            "price": tech.get("price"),
            "data_delay_note": "all market data delayed; see per-layer timestamps",
            "tactical": {
                "audience_warning": TACTICAL_WARNING,
                "data": tac_data,
                "narrative": reads["tactical"],
            },
            "structural": {
                "audience_warning": STRUCTURAL_WARNING,
                "data": struct_data,
                "narrative": reads["structural"],
            },
            "layers": {
                "technicals": tech, "supply_demand": sd, "curve": curve,
                "positioning": pos, "seasonality": season, "macro": mac,
            },
        }
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    args = [a for a in sys.argv[1:]]
    with_llm = "--no-llm" not in args
    keys = [a for a in args if a in COMMODITIES] or None
    result = run(keys, with_llm=with_llm)
    out_dir = Path(__file__).resolve().parent / "data" / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "latest_analysis.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    log.info("written -> %s", path)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
