-- ============================================================
-- 015_garch_forecasts.sql
-- GARCH(1,1) volatility forecasts from the Quantitative Models Desk
-- (pipeline/quant/garch.py).
--
-- Numbered 015 (the next sequential migration after 014). The original
-- request said 018 -- renumbered to the real sequence, same convention
-- as 002-006 / 013 / 014.
--
-- GARCH forecasts FORWARD volatility (volatility clustering) rather than
-- the backward-looking realized volatility. All numbers are produced by
-- the `arch` package; only `explanation` is LLM-written.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.garch_forecasts (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker              text NOT NULL,
  forecast_date       date NOT NULL DEFAULT current_date,
  model_alpha         numeric,        -- ARCH coefficient
  model_beta          numeric,        -- GARCH coefficient
  model_persistence   numeric,        -- alpha + beta (near 1 = unstable)
  realized_vol_30d    numeric,        -- annualized %, backward-looking
  forecast_vol_1d     numeric,        -- annualized %
  forecast_vol_5d     numeric,        -- annualized %
  forecast_vol_22d    numeric,        -- annualized %, ~30 calendar days
  unconditional_vol   numeric,        -- annualized %, long-term average
  regime              text,           -- low | normal | elevated | crisis
  iv_vs_garch_spread  numeric,        -- IV - GARCH; positive = options expensive
  model_quality       text,           -- reliable | use_with_caution | unreliable
  explanation         text,           -- LLM-generated
  created_at          timestamptz DEFAULT now(),
  UNIQUE (ticker, forecast_date)
);

CREATE INDEX IF NOT EXISTS idx_garch_forecasts_date
  ON public.garch_forecasts (forecast_date DESC, ticker);

ALTER TABLE public.garch_forecasts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.garch_forecasts
  FOR SELECT TO authenticated USING (true);
