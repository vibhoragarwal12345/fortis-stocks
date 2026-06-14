"""India macro context (no key) via the World Bank API: CPI inflation and GDP.
For the report's macro backdrop only -- not used in any per-stock number."""
from __future__ import annotations
import json, sys
sys.stdout.reconfigure(encoding="utf-8")
import requests

IND = "https://api.worldbank.org/v2/country/IND/indicator/{}?format=json&per_page=8&mrnev=5"
SERIES = {"FP.CPI.TOTL.ZG": "cpi_inflation_pct", "NY.GDP.MKTP.CD": "gdp_usd",
          "NY.GDP.MKTP.KD.ZG": "gdp_growth_pct"}

def fetch(code):
    r = requests.get(IND.format(code), timeout=20); r.raise_for_status()
    j = r.json()
    if len(j) < 2 or not j[1]:
        return None
    return [{"year": d["date"], "value": d["value"]} for d in j[1] if d["value"] is not None]

def main():
    out = {}
    for code, name in SERIES.items():
        try:
            series = fetch(code)
            out[name] = series
            latest = series[0] if series else None
            print(f"  {name:<18} latest {latest['year'] if latest else '?'} = {latest['value'] if latest else '?'}")
        except Exception as e:
            out[name] = {"error": str(e)[:100]}; print(f"  {name} ERROR {e}")
    json.dump({"source": "World Bank API (no key)", "series": out},
              open(r"D:\macro_context.json","w",encoding="utf-8"), indent=2)
    print("Saved D:\\macro_context.json")

if __name__ == "__main__":
    main()
