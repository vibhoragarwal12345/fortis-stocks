-- ============================================================
-- 014_risk_metrics.sql
-- VaR / CVaR risk output from the Quantitative Models Desk
-- (pipeline/quant/var_cvar.py): per-ticker and per-portfolio.
--
-- Numbered 014 (the next sequential migration after 013). The original
-- request said 017 -- renumbered to the real sequence, same convention
-- as 002-006 / 013.
--
-- VaR / CVaR columns are stored as NEGATIVE numbers (a loss). All values
-- are produced by deterministic Python (numpy / scipy).
-- ============================================================

CREATE TABLE IF NOT EXISTS public.ticker_risk_metrics (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker             text NOT NULL,
  calculation_date   date NOT NULL DEFAULT current_date,
  daily_var_95       numeric,        -- negative = loss
  daily_var_99       numeric,        -- negative = loss
  daily_cvar_95      numeric,        -- negative = loss
  weekly_var_95      numeric,        -- 5-day, sqrt-scaled, negative
  annual_volatility  numeric,        -- annualized stdev
  max_drawdown_1y    numeric,        -- negative = peak-to-trough drop
  sharpe_ratio_1y    numeric,
  sortino_ratio_1y   numeric,
  beta_vs_spy        numeric,        -- 252-day OLS slope vs SPY
  var_floored        boolean DEFAULT false,   -- VAR_FLOOR applied (data artifact guard)
  var_exceedances_1y int,            -- realized days worse than VaR_95 (expect ~12-13)
  miscalibrated      boolean DEFAULT false,
  data_quality_score numeric,        -- 0..1
  created_at         timestamptz DEFAULT now(),
  UNIQUE (ticker, calculation_date)
);

CREATE INDEX IF NOT EXISTS idx_ticker_risk_metrics_date
  ON public.ticker_risk_metrics (calculation_date DESC, ticker);

CREATE TABLE IF NOT EXISTS public.portfolio_risk_metrics (
  id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  portfolio_id             uuid,                  -- NULL for the synthetic demo portfolio
  portfolio_label          text,
  calculation_date         date NOT NULL DEFAULT current_date,
  portfolio_notional_usd   numeric,
  portfolio_var_95_1d_pct  numeric,
  portfolio_var_95_1d_usd  numeric,
  portfolio_var_99_1d_pct  numeric,
  portfolio_var_99_1d_usd  numeric,
  portfolio_cvar_95_pct    numeric,
  portfolio_cvar_95_usd    numeric,
  annual_volatility        numeric,
  diversification_ratio    numeric,               -- 1 = no benefit, higher = better
  stress_test_gfc_2008     numeric,                -- estimated drawdown %
  stress_test_covid_2020   numeric,
  stress_test_rate_shock_2022 numeric,
  top_3_risk_contributors  jsonb DEFAULT '[]'::jsonb,
  created_at               timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_risk_metrics_date
  ON public.portfolio_risk_metrics (calculation_date DESC);

ALTER TABLE public.ticker_risk_metrics    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_risk_metrics ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.ticker_risk_metrics
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_read" ON public.portfolio_risk_metrics
  FOR SELECT TO authenticated USING (true);
