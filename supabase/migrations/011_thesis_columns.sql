-- ============================================================
-- 011_thesis_columns.sql
-- Adds the institutional-thesis columns written by
-- pipeline/agents/debate_synthesizer.py to ranked_focus_list.
--
-- Numbered 011 (the next sequential migration after 010). Purely additive --
-- ranked_focus_list comes from migration 001, catalyst columns from 010.
-- ============================================================

ALTER TABLE public.ranked_focus_list
  ADD COLUMN IF NOT EXISTS bull_case             text,
  ADD COLUMN IF NOT EXISTS bear_case             text,
  ADD COLUMN IF NOT EXISTS price_target_upside   numeric,   -- 3-month upside price level
  ADD COLUMN IF NOT EXISTS price_target_downside numeric,   -- downside risk price level
  ADD COLUMN IF NOT EXISTS position_size_guidance text,     -- suggested portfolio weight
  ADD COLUMN IF NOT EXISTS what_to_watch         jsonb,     -- array of 3 confirm/invalidate items
  ADD COLUMN IF NOT EXISTS thesis_generated_at   timestamptz;

COMMENT ON COLUMN public.ranked_focus_list.position_size_guidance IS
  'Suggested portfolio weight band (low 0-1% / medium 1-3% / high 3-5%). '
  'Sizing guidance, not investment advice.';
