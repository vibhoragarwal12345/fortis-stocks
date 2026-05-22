-- ============================================================
-- 032_pick_outcomes_smart_money.sql
-- Adds smart_money_pattern + composite_smart_money_score to pick_outcomes so
-- the backtest engine can answer "which Smart Money patterns produced the
-- best forward returns?" once 60-90 days of data has matured.
-- ============================================================

ALTER TABLE public.pick_outcomes
  ADD COLUMN IF NOT EXISTS smart_money_pattern         text,
  ADD COLUMN IF NOT EXISTS composite_smart_money_score numeric,
  ADD COLUMN IF NOT EXISTS smart_money_confidence      text;

CREATE INDEX IF NOT EXISTS idx_pick_outcomes_smart_pattern
  ON public.pick_outcomes (smart_money_pattern, recommended_date DESC)
  WHERE smart_money_pattern IS NOT NULL;

COMMENT ON COLUMN public.pick_outcomes.smart_money_pattern IS
  'Pattern from smart_money_intel at pick time. Joined with forward returns '
  'after 60-90 days lets the backtest engine learn which patterns predict '
  'outperformance and feed Tier 5 weight learning.';
