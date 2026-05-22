-- ============================================================
-- 025_conviction_v2_columns.sql
-- Adds the v2 conviction-grade columns written by
-- pipeline/agents/conviction_grader_v2.py to ranked_focus_list.
--
-- Numbered 025 (the next sequential migration after 024). Purely additive.
--
-- The v1 grader (conviction_grader.py, migration 019) percentile-grades a
-- quant-bonus score and is preserved alongside the v2 columns; both write to
-- the same ranked_focus_list rows but to disjoint columns:
--
--   v1 -- conviction_score, quant_signal_summary, conviction_grade
--   v2 -- conviction_score_adjusted, grade_reasoning, conviction_grade
--
-- conviction_grade is shared. Whichever grader runs last "wins" the grade,
-- so wire the pipeline accordingly (in run_full_pipeline.py).
-- ============================================================

ALTER TABLE public.ranked_focus_list
  ADD COLUMN IF NOT EXISTS conviction_score_adjusted numeric, -- score after all v2 multipliers
  ADD COLUMN IF NOT EXISTS grade_reasoning           text;    -- one-sentence justification

COMMENT ON COLUMN public.ranked_focus_list.conviction_score_adjusted IS
  'v2 grader (conviction_grader_v2.py): validated_strength * multipliers from '
  'pattern_matches.win_rate_5d, catalyst_confidence, factor_concentration_warning. '
  'Maps to A/B/C with hard caps -- STRONG critic objection forces C.';

COMMENT ON COLUMN public.ranked_focus_list.grade_reasoning IS
  'One-sentence advisor-facing justification for the conviction_grade, '
  'naming the dominant signals (confirmation count, catalyst, analogs, critic).';
