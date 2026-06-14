"""Authoritative data-source connection report (hand-verified from live smoke
tests on 2026-06-09). This is the single source of truth the PDF cites for what
is VERIFIED vs [UNVERIFIED]."""
import json
from datetime import datetime, timezone

S = {
 "as_of_utc": datetime.now(timezone.utc).isoformat(),
 "sources": {
  "yfinance_price": {"status":"CONNECTED","role":"PRIMARY live price + 2y history",
     "note":"Fresh to today (BEL.NS 2026-06-09 close ₹411.60). EOD/delayed, not real-time tick."},
  "yfinance_fundamentals": {"status":"CONNECTED-WITH-CAVEAT","role":"P/E,P/B,EV/EBITDA,ROE,margins",
     "note":"Known .NS bug: financialCurrency can be USD while price is INR -> ratios wrong. "
            "Each field currency-checked; mismatches marked UNVERIFIED. Not a licensed feed."},
  "jugaad_eod": {"status":"CONNECTED-BUT-LAGGED","role":"redundant NSE EOD cross-check",
     "note":"Works after cache-race fix, but NSE free history trails ~3-4 weeks (last 2026-05-14). "
            "Used to validate yfinance historical closes, NOT as a current-price source."},
  "jugaad_live_quote": {"status":"FAILED","role":"redundant live quote",
     "note":"NSE anti-bot returns non-JSON; nsefetch quote-equity priceInfo empty. yfinance covers price."},
  "nse_announcements": {"status":"CONNECTED","role":"Reg-30 filings / order wins / results catalysts",
     "note":"nsepython nsefetch corporate-announcements. BEL returned 1005 rows. Company-verified."},
  "nse_financial_results": {"status":"CONNECTED","role":"results-filing presence",
     "note":"corporates-financial-results endpoint live (BEL 107 rows). Headline figures need a "
            "licensed fundamentals feed to parse reliably."},
  "nse_pit_insider": {"status":"CONNECTED","role":"SEBI PIT insider buy/sell",
     "note":"corporates-pit live (BEL 13 rows). Net BUY/SELL derived from counts (no hardcoded label)."},
  "nse_sast": {"status":"CONNECTED-SPARSE","role":"SAST substantial-acquisition disclosures",
     "note":"corporate-sast endpoint returns 200 but BEL had 0 rows; populated only for names with "
            "recent 5%+ moves. Available where data exists; absence != error."},
  "nse_fno_ban": {"status":"CONNECTED","role":"F&O ban (crowding proxy)",
     "note":"fo_secban.csv live (63 bytes => empty ban list that day, which is itself a valid read)."},
  "nseindiaapi_fallback": {"status":"CONNECTED","role":"announcements/board-meeting fallback",
     "note":"pip 'nse' NSE() returned 20 market-wide announcements. Backup if nsefetch flaky."},
  "google_news_rss": {"status":"CONNECTED","role":"news flow per holding (chatter)",
     "note":"100 entries for BEL query. Used for sentiment/Signal-vs-Chatter only, not as fact."},
  "vader_sentiment": {"status":"CONNECTED","role":"local headline sentiment",
     "note":"Runs offline; compound score per headline. FinBERT optional upgrade."},
  "worldbank_macro": {"status":"CONNECTED","role":"India CPI/GDP macro context",
     "note":"World Bank API live (India CPI 2024 = 4.95%). No key needed."},
 },
 "paid_feed_recommendation": {
  "primary_pick": "Screener.in (~Rs4,999/yr)",
  "why":"Cleanest standardised Indian fundamentals (10y P&L/BS/CF, ratios, peer compare) and an "
        "export/API path; unlocks genuine valuation (PEG, ROCE trend, debt schedule, fwd estimates) "
        "that yfinance .NS cannot be trusted for.",
  "alternatives":["Tijori (~Rs3,500/yr, segment/capacity data)","Trendlyne (estimates+DVM)",
                  "Tickertape (cheap, decent ratios)"],
  "wire_steps":[
    "1. Subscribe Screener; from Account -> get the export/API access (or use the official data export).",
    "2. Store key in env: setx SCREENER_API_KEY <key> (never hard-code in repo).",
    "3. Add pipeline/india/fundamentals_screener.py: GET company consolidated data by symbol; "
       "map to {pe,pb,ev_ebitda,roce,roe,debt_eq,sales_cagr,profit_cagr,promoter_pledge}.",
    "4. In analyze.py, prefer Screener fields over yfinance; keep yfinance only as fallback and "
       "drop the currency-sanity guard for Screener (already INR, standardised).",
    "5. Re-run; the PDF's 'Fundamentals read' blocks flip from UNVERIFIED to verified valuation."],
 },
}
json.dump(S, open(r"D:\source_status.json","w",encoding="utf-8"), indent=2)
print("Wrote D:\\source_status.json")
for k,v in S["sources"].items():
    print(f"  {v['status']:<22} {k}")
