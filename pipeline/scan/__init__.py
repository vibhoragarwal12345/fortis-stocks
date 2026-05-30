"""
Two-speed funnel for the lean platform.
========================================

A complete scan executes four layers, in order:

    layer1_fast_scan  All ~3-5k tickers in the liquid universe. Cheap math
                      only -- price action, volume, RSI, 52w, breakout. No
                      LLM. Target: under 30 minutes for the full universe.
    layer2_rank       Composite score across the layer-1 metrics. Take the
                      top 80 and write them to ranked_focus_list with a
                      scan_id stamp.
    layer3_deep       Run the existing expensive agents (catalyst, smart
                      money, debate, critic) plus per-ticker quant on the
                      top 80 only. This is where the LLM tokens get spent.
    layer4_grade      conviction_grader + factcheck on the deeply-analyzed
                      shortlist. Writes the final A / B / C grades.

run_scan.py is the single entry point that orchestrates the four. Cron'd
every 2 hours during market hours via .github/workflows/market_scan.yml.

NO REPORTS. The "report" is the live dashboard reading the latest
market_scans row tagged status='complete'.
"""
