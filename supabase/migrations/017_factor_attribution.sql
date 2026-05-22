-- ============================================================
-- 017_factor_attribution.sql
-- Fama-French 5-factor regression output from the Quantitative Models
-- Desk (pipeline/quant/fama_french.py).
--
-- Numbered 017 (the next sequential migration after 016). The original
-- request said 019 -- renumbered to the real sequence, same convention
-- as every prior migration.
--
-- All betas, t-stats, alpha and R-squared are produced by statsmodels OLS
-- against Ken French's published daily factor series. No LLM involved.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.factor_attribution (
  id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                 text NOT NULL,
  calculation_date       date NOT NULL DEFAULT current_date,
  beta_market            numeric,   -- Mkt-RF loading
  beta_size              numeric,   -- SMB loading
  beta_value             numeric,   -- HML loading
  beta_profitability     numeric,   -- RMW loading
  beta_investment        numeric,   -- CMA loading
  t_market               numeric,
  t_size                 numeric,
  t_value                numeric,
  t_profitability        numeric,
  t_investment           numeric,
  alpha_daily            numeric,
  alpha_annual           numeric,
  alpha_significance     text,      -- significant_positive | significant_negative | insignificant
  r_squared              numeric,   -- explanatory power of the 5 factors
  regression_window_days int,
  factor_label           text,      -- e.g. "Large-cap growth, market-like, high-profitability"
  factor_box             text,      -- large_value | large_growth | small_value | small_growth | mid_blend
  created_at             timestamptz DEFAULT now(),
  UNIQUE (ticker, calculation_date)
);

CREATE INDEX IF NOT EXISTS idx_factor_attribution_date
  ON public.factor_attribution (calculation_date DESC, ticker);

ALTER TABLE public.factor_attribution ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.factor_attribution
  FOR SELECT TO authenticated USING (true);
