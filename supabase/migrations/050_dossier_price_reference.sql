-- ============================================================
-- 050_dossier_price_reference.sql
-- Honest statistical price reference band for each focus-list pick.
--
-- Replaces the LLM-asserted price_target_upside/downside (which only ~half
-- the names ever got, and which were numbers the model produced rather than
-- traced to data) with a deterministic zero-drift Monte Carlo reference cone
-- computed in the scan (pipeline/scan/price_bands.py) for EVERY shortlist
-- name, regardless of the LLM budget.
--
-- price_reference jsonb shape:
--   {
--     "low":          numeric,  -- 5th percentile simulated price
--     "mid":          numeric,  -- median (≈ basis, drift is zeroed)
--     "high":         numeric,  -- 95th percentile simulated price
--     "basis":        numeric,  -- last close the cone was anchored on
--     "horizon_days": int,      -- simulation horizon (trading days)
--     "method":       text,     -- 'monte_carlo_zero_drift'
--     "distribution": text,     -- 'normal' | 'student_t'
--     "calibration":  numeric,  -- 0..1 goodness-of-fit (1 - KS stat)
--     "generated_at": text      -- ISO timestamp
--   }
-- Deterministic numpy/scipy output -- never LLM-generated. The dossier gate
-- requires it; the UI labels it "statistical reference range, not a forecast".
-- ============================================================

ALTER TABLE public.ranked_focus_list
  ADD COLUMN IF NOT EXISTS price_reference jsonb;

COMMENT ON COLUMN public.ranked_focus_list.price_reference IS
  'Zero-drift Monte Carlo price reference cone (deterministic; pipeline/scan/price_bands.py). Statistical reference range, not a forecast.';
