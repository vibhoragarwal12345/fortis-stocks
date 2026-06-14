-- ============================================================
-- 051_drop_orphan_portfolio_tables.sql
-- Drop tables orphaned by the removed Black-Litterman / portfolio-optimization
-- desk. The code that wrote them is gone:
--   * pipeline/quant/black_litterman.py   (deleted)
--   * pipeline/agents/portfolio_fit.py    (deleted)
-- No application or pipeline code references portfolio_optimization (020) or
-- portfolio_fit (023) — verified by grep across src/ + pipeline/; only their
-- own CREATE migrations and stale docs mentioned them.
--
-- NOTE: factor_exposure (022) is deliberately NOT dropped — it is live
-- (pipeline/agents/factor_overlay.py writes it; feedback_aggregator +
-- peer_definition read it).
-- ============================================================

DROP TABLE IF EXISTS public.portfolio_optimization CASCADE;
DROP TABLE IF EXISTS public.portfolio_fit          CASCADE;
