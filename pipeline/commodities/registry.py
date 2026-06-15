"""
Commodity registry — the single source of truth for which commodities the
pipeline covers and how each one maps onto every data source.

Every ingestor in sources/ keys off this dict so a commodity is added or
removed in exactly one place. All commodities are priced vs USD.

Field reference:
  category        energy | metals | ags  (drives dashboard grouping)
  yf_symbol       yfinance front-month continuous future, None if unreliable
  fred_spot       FRED series used to cross-check the yfinance price.
                  Frequency noted because a daily future vs a monthly index
                  is a sanity check, not a tick-level comparison.
  cftc_code       CFTC contract market code in the legacy futures-only COT
                  dataset (publicreporting.cftc.gov / 6dca-aqww).
  cftc_name_hint  substring fallback if the code returns nothing
  news_query      Google News RSS query for the narrative layer
"""

COMMODITIES: dict[str, dict] = {
    "wti": {
        "name": "Crude Oil (WTI)",
        "category": "energy",
        "yf_symbol": "CL=F",
        "fred_spot": {"series": "DCOILWTICO", "label": "WTI spot (EIA via FRED)", "freq": "daily"},
        "cftc_code": "067651",
        "cftc_name_hint": "CRUDE OIL, LIGHT SWEET",
        "news_query": "WTI crude oil price OPEC",
    },
    "brent": {
        "name": "Crude Oil (Brent)",
        "category": "energy",
        "yf_symbol": "BZ=F",
        "fred_spot": {"series": "DCOILBRENTEU", "label": "Brent spot (EIA via FRED)", "freq": "daily"},
        "cftc_code": None,  # ICE Brent reports to ICE Futures Europe, not in the CFTC legacy set — fall back to name search
        "cftc_name_hint": "BRENT",
        "news_query": "Brent crude oil price",
    },
    "natgas": {
        "name": "Natural Gas (Henry Hub)",
        "category": "energy",
        "yf_symbol": "NG=F",
        "fred_spot": {"series": "DHHNGSP", "label": "Henry Hub spot (EIA via FRED)", "freq": "daily"},
        "cftc_code": "023651",
        "cftc_name_hint": "NATURAL GAS",
        "news_query": "natural gas price Henry Hub storage",
    },
    "gold": {
        "name": "Gold",
        "category": "metals",
        "yf_symbol": "GC=F",
        # LBMA fix series were removed from FRED (400 as of 2026-06-12) — gold
        # has no free independent spot cross-check; the layer stays UNVERIFIED.
        "fred_spot": None,
        "cftc_code": "088691",
        "cftc_name_hint": "GOLD",
        "news_query": "gold price central bank fed",
    },
    "silver": {
        "name": "Silver",
        "category": "metals",
        "yf_symbol": "SI=F",
        # LBMA silver series also gone from FRED (400 as of 2026-06-12).
        "fred_spot": None,
        "cftc_code": "084691",
        "cftc_name_hint": "SILVER",
        "news_query": "silver price",
    },
    "copper": {
        "name": "Copper",
        "category": "metals",
        "yf_symbol": "HG=F",
        "fred_spot": {"series": "PCOPPUSDM", "label": "Global copper price, IMF (FRED)", "freq": "monthly"},
        "cftc_code": "085692",
        "cftc_name_hint": "COPPER",
        "news_query": "copper price LME demand",
    },
    # NOTE: iron_ore was removed 2026-06-15 (owner directive). On free data it
    # had no daily price (no yf future), no COT, no curve, no seasonality and no
    # daily technicals — 5 of 6 analysis layers came back empty/UNVERIFIED and
    # the tactical narrative could only say it had nothing to assess. It read as
    # a near-empty card next to the other metals, so it was dropped rather than
    # ship a hollow tile. Re-add here if a reliable free price/positioning feed
    # appears.
    "soybeans": {
        "name": "Soybeans",
        "category": "ags",
        "yf_symbol": "ZS=F",
        "fred_spot": {"series": "PSOYBUSDM", "label": "Global soybeans price, IMF (FRED)", "freq": "monthly"},
        "cftc_code": "005602",
        "cftc_name_hint": "SOYBEANS",
        "news_query": "soybean price harvest exports",
        "nass_commodity": "SOYBEANS",
    },
    "corn": {
        "name": "Corn",
        "category": "ags",
        "yf_symbol": "ZC=F",
        "fred_spot": {"series": "PMAIZMTUSDM", "label": "Global corn price, IMF (FRED)", "freq": "monthly"},
        "cftc_code": "002602",
        "cftc_name_hint": "CORN",
        "news_query": "corn price harvest USDA",
        "nass_commodity": "CORN",
    },
    "wheat": {
        "name": "Wheat (Chicago SRW)",
        "category": "ags",
        "yf_symbol": "ZW=F",
        "fred_spot": {"series": "PWHEAMTUSDM", "label": "Global wheat price, IMF (FRED)", "freq": "monthly"},
        "cftc_code": "001602",
        "cftc_name_hint": "WHEAT-SRW",
        "news_query": "wheat price exports Black Sea",
        "nass_commodity": "WHEAT",
    },
    "coffee": {
        "name": "Coffee (Arabica)",
        "category": "ags",
        "yf_symbol": "KC=F",
        "fred_spot": {"series": "PCOFFOTMUSDM", "label": "Global coffee price (other mild arabica), IMF (FRED)", "freq": "monthly"},
        "cftc_code": "083731",
        "cftc_name_hint": "COFFEE",
        "news_query": "coffee price arabica Brazil Vietnam",
        # USDA NASS coverage of coffee is thin (only HI/PR production) — the
        # supply/demand layer for coffee is news + price only, flagged limited.
        "nass_commodity": None,
    },
}


def by_category() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"energy": [], "metals": [], "ags": []}
    for key, c in COMMODITIES.items():
        out[c["category"]].append(key)
    return out
