"""
Multibagger Discovery Engine
============================

This engine hunts for asymmetric long-horizon opportunities. It will be wrong
most of the time on individual names -- that is expected and acceptable,
because the winners can return many multiples while losers can only return
-100%. The product's honesty about this asymmetry is its integrity. Never let
this engine's output be framed as high-confidence prediction. It is
STRUCTURED SPECULATION, rigorously researched and honestly risk-framed.

This package is INTENTIONALLY SEPARATE from the daily conviction grader.
A future multibagger scores TERRIBLY on cross-source confirmation (that's WHY
it's still cheap). Different philosophy, different scoring, different cadence.

Pipeline (cadence changed monthly -> weekly June 2026; quarterly review removed):
    universe.py          weekly  -- build the broader small/micro-cap universe
    screener.py          weekly  -- 6 multibagger DNA traits
    scorer.py            weekly  -- multibagger_score with hard caps
    deep_research.py     weekly  -- LLM thesis for the top N, balanced + honest
    watchlist_manager.py weekly  -- add / update / graduate / archive candidates
    tracker.py           weekly  -- honest return tracking incl. failures
    reports.py           weekly  -- Emerging Opportunities report
"""
