-- ============================================================
-- 012_critic_columns.sql
-- Adds the devil's-advocate critique columns written by
-- pipeline/agents/critic_agent.py to ranked_focus_list.
--
-- Numbered 012 (the next sequential migration after 011). Purely additive.
--
-- critic_agent challenges every thesis with a deliberately different model
-- so the advisor never reads one-sided cheerleading. critic_objection_level
-- feeds the conviction grade (STRONG_OBJECTION caps the grade at B).
-- critic_rewritten_bear_case is the sharper bear case that replaces the
-- thesis writer's original bear_case.
-- ============================================================

ALTER TABLE public.ranked_focus_list
  ADD COLUMN IF NOT EXISTS critic_weaknesses          text,
  ADD COLUMN IF NOT EXISTS critic_disconfirming_data  text,
  ADD COLUMN IF NOT EXISTS critic_precedent_failures  text,
  ADD COLUMN IF NOT EXISTS critic_hidden_risks        text,
  ADD COLUMN IF NOT EXISTS critic_objection_level     text,  -- STRONG | MODERATE | NONE
  ADD COLUMN IF NOT EXISTS critic_rewritten_bear_case text;

COMMENT ON COLUMN public.ranked_focus_list.critic_objection_level IS
  'Devil''s-advocate verdict: STRONG (caps conviction grade at B), '
  'MODERATE (max A, flagged for advisor review), or NONE.';
