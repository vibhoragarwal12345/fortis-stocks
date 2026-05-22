-- ============================================================
-- 020_portfolio_optimization.sql
-- Black-Litterman portfolio-optimization output from the Quantitative
-- Models Desk (pipeline/quant/black_litterman.py).
--
-- Numbered 020 (the next sequential migration after 019). The original
-- request said 021 -- renumbered to the real sequence.
--
-- All optimization maths is deterministic (numpy Bayesian posterior +
-- cvxpy constrained QP). Only `explanation` is LLM-written.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.portfolio_optimization (
  id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  portfolio_id                uuid,                  -- NULL for the synthetic demo
  portfolio_label             text,
  run_date                    date NOT NULL DEFAULT current_date,
  run_type                    text NOT NULL DEFAULT 'midday',
  current_weights             jsonb,                 -- {ticker: weight}
  optimal_weights             jsonb,                 -- {ticker: weight}
  suggested_trades            jsonb DEFAULT '[]'::jsonb,  -- [{ticker,current,target,action}]
  expected_return_current     numeric,
  expected_return_optimal     numeric,
  expected_volatility_current numeric,
  expected_volatility_optimal numeric,
  sharpe_current              numeric,
  sharpe_optimal              numeric,
  max_drawdown_current        numeric,
  max_drawdown_optimal        numeric,
  transaction_cost_estimate   numeric,               -- 5bps round-trip assumption
  net_benefit                 numeric,               -- improvement minus costs
  views_applied               jsonb DEFAULT '[]'::jsonb,
  optimization_quality        text,                  -- optimal_solution | feasible_but_constrained | infeasible
  explanation                 text,                  -- LLM-generated
  created_at                  timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_optimization_date
  ON public.portfolio_optimization (run_date DESC);

ALTER TABLE public.portfolio_optimization ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.portfolio_optimization
  FOR SELECT TO authenticated USING (true);
