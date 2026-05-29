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

Pipeline:
    universe.py          monthly  -- build the broader small/micro-cap universe
    screener.py          monthly  -- 6 multibagger DNA traits
    scorer.py            monthly  -- multibagger_score with hard caps
    deep_research.py     monthly  -- LLM thesis for the top 30, balanced + honest
    watchlist_manager.py weekly   -- add / update / graduate / archive candidates
    quarterly_review.py  quarterly-- re-score, thesis status, post-mortems
    tracker.py           weekly   -- honest return tracking incl. failures
    reports.py           monthly+quarterly -- Emerging Opportunities + Quarterly Review
"""
