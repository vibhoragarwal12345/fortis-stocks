-- ============================================================
-- 023_portfolio_fit.sql
-- Per-portfolio fit analysis from pipeline/agents/portfolio_fit.py.
--
-- Numbered 023 (the next sequential migration after 022). The original
-- request said 012, but 012 is long-applied (critic_columns) -- so this
-- follows the real sequence, same convention as every prior migration.
--
-- One row per (portfolio, ticker, snapshot_date) -- each candidate ticker
-- scored against the actual holdings of one user's portfolio.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.portfolio_fit (
  id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  portfolio_id          uuid REFERENCES public.portfolios,
  ticker                text NOT NULL,
  snapshot_date         date NOT NULL DEFAULT current_date,
  already_held          boolean DEFAULT false,
  current_weight        numeric,    -- 0..1 (fraction of portfolio)
  unrealized_pnl        numeric,    -- (current_price - cost_basis) / cost_basis
  sector_fit_score      numeric,    -- 0..100  (100 = no over-concentration risk)
  correlation_warnings  jsonb DEFAULT '[]'::jsonb,  -- [{holding,corr}] for corr > 0.85
  style_alignment       text,       -- aligned | mild_drift | significant_drift
  risk_contribution     numeric,    -- portfolio vol delta if added at 2% (annualized %)
  recommended_size      numeric,    -- suggested weight (0..0.04)
  recommendation        text,       -- high_priority | consider | pass | already_holding
  created_at            timestamptz DEFAULT now(),
  UNIQUE (portfolio_id, ticker, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_fit_date
  ON public.portfolio_fit (snapshot_date DESC, portfolio_id);

ALTER TABLE public.portfolio_fit ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.portfolio_fit
  FOR SELECT TO authenticated USING (true);
