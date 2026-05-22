-- ============================================================
-- 010_catalyst_columns.sql
-- Adds the catalyst columns written by
-- pipeline/agents/catalyst_agent.py to the existing ranked_focus_list.
--
-- Numbered 010 (the next sequential migration after 009). Purely additive --
-- ranked_focus_list itself comes from migration 001.
--
-- The catalyst is the single specific event driving a ticker's signal today;
-- it turns the focus list from a screener output into an analyst note.
-- ============================================================

ALTER TABLE public.ranked_focus_list
  ADD COLUMN IF NOT EXISTS catalyst_category        text,    -- earnings | sec_event | analyst_action | ...
  ADD COLUMN IF NOT EXISTS catalyst_description     text,    -- 1-2 sentence analyst-style note
  ADD COLUMN IF NOT EXISTS catalyst_confidence      numeric, -- 0..1, how clear the catalyst is
  ADD COLUMN IF NOT EXISTS catalyst_supporting_data jsonb;   -- raw data behind the call

COMMENT ON COLUMN public.ranked_focus_list.catalyst_category IS
  'Highest-priority catalyst type: earnings, sec_event, analyst_action, '
  'technical_event, news_driven, options_driven, insider_institutional, '
  'social_momentum, or none';
COMMENT ON COLUMN public.ranked_focus_list.catalyst_confidence IS
  '0..1 confidence that this is the real driver -- earnings/SEC score high, '
  'pure social-momentum scores low';
