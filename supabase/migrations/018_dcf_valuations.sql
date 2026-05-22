-- ============================================================
-- 018_dcf_valuations.sql
-- Discounted-cash-flow intrinsic valuations from the Quantitative
-- Models Desk (pipeline/quant/dcf.py).
--
-- Numbered 018 (the next sequential migration after 017). The original
-- request said 020 -- renumbered to the real sequence.
--
-- DCF is built with deliberately conservative defaults (growth capped at
-- 15%, terminal 2.5%, WACC capped at 12%). The `limitations` column always
-- states, in plain language, what the model could be getting wrong.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.dcf_valuations (
  id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker               text NOT NULL,
  calculation_date     date NOT NULL DEFAULT current_date,
  current_price        numeric,
  dcf_value_per_share  numeric,
  dcf_low              numeric,    -- bottom of the sensitivity range
  dcf_high             numeric,    -- top of the sensitivity range
  upside_to_dcf_pct    numeric,    -- (dcf - price) / price
  valuation_label      text,       -- deeply_undervalued | undervalued | fairly_valued | overvalued | deeply_overvalued | not_applicable
  assumptions          jsonb,      -- growth, wacc, terminal_growth, base_fcf
  peer_multiple_check  text,       -- consistent_with_peers | outlier_high | outlier_low
  confidence           text,       -- high | medium | low
  limitations          text,       -- explicit statement of model weaknesses
  created_at           timestamptz DEFAULT now(),
  UNIQUE (ticker, calculation_date)
);

CREATE INDEX IF NOT EXISTS idx_dcf_valuations_date
  ON public.dcf_valuations (calculation_date DESC, ticker);

ALTER TABLE public.dcf_valuations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.dcf_valuations
  FOR SELECT TO authenticated USING (true);
