"""Backtest v2 — point-in-time replay of the production scan funnel.

See docs/BACKTEST_REBUILD_PLAN.md. The core invariant: this package imports
the REAL layer-1 metric and layer-2 scoring functions from pipeline/scan/, so
the backtest can never drift from the product the way v1 did.
"""
