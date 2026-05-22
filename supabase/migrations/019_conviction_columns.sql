-- ============================================================
-- 019_conviction_columns.sql
-- Conviction-grade columns written by pipeline/agents/conviction_grader.py
-- onto the existing ranked_focus_list.
--
-- Numbered 019 (the next sequential migration after 018).
--
-- conviction_grader is the agent that ENFORCES the verdicts the critic,
-- GARCH and quant desks only stored: it consolidates them into a single
-- A/B/C/D grade with hard caps. quant_signal_summary carries the per-model
-- traffic lights that drive the "Quant Models Verdict" report section.
-- ============================================================

ALTER TABLE public.ranked_focus_list
  ADD COLUMN IF NOT EXISTS conviction_grade      text,    -- A | B | C | D
  ADD COLUMN IF NOT EXISTS conviction_score      numeric, -- base score + quant bonuses
  ADD COLUMN IF NOT EXISTS quant_signal_summary  jsonb;   -- bonuses, caps, model lights

COMMENT ON COLUMN public.ranked_focus_list.conviction_grade IS
  'A/B/C/D conviction grade. Hard-capped: STRONG critic objection -> max B; '
  'GARCH crisis -> max C; GARCH elevated / low MC calibration / cross-'
  'validation contradiction -> max B.';
