-- ============================================================
-- 013_monte_carlo_results.sql
-- Monte Carlo price-simulation output from the Quantitative Models Desk
-- (pipeline/quant/monte_carlo.py).
--
-- Numbered 013 (the next sequential migration after 012). The original
-- request said 016, but 001-012 are the only prior migrations -- a
-- numbering gap would break ordered migration runners, so this follows
-- the same renumbering convention used by 002-006.
--
-- Every numeric column here is produced by deterministic Python (numpy /
-- scipy). Only `explanation` is LLM-written -- and only to rephrase these
-- already-computed numbers, never to introduce new ones.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.monte_carlo_results (
  id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                text NOT NULL,
  run_date              date NOT NULL DEFAULT current_date,
  run_type              text NOT NULL DEFAULT 'midday',
  horizon_days          int  NOT NULL,                 -- 1 | 5 | 30
  starting_price        numeric,
  distribution_type     text,                          -- normal | student_t
  mean_price            numeric,
  median_price          numeric,
  p5_price              numeric,
  p25_price             numeric,
  p75_price             numeric,
  p95_price             numeric,
  prob_up               numeric,                       -- 0..1
  prob_loss_10pct       numeric,                       -- 0..1
  prob_gain_10pct       numeric,                       -- 0..1
  expected_max_drawdown numeric,                       -- negative fraction
  calibration_quality   numeric,                       -- 0..1
  explanation           text,                          -- LLM-generated
  n_simulations         int,
  data_lookback_days    int,
  created_at            timestamptz DEFAULT now(),
  UNIQUE (ticker, run_date, run_type, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_monte_carlo_results_run
  ON public.monte_carlo_results (run_date DESC, ticker);

ALTER TABLE public.monte_carlo_results ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.monte_carlo_results
  FOR SELECT TO authenticated USING (true);
