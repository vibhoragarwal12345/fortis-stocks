-- ============================================================
-- 027_peer_industry_analysis.sql
-- Per-A-pick three-dimensional verdict produced by
-- pipeline/agents/peer_industry_analysis.py
--
-- Numbered 027 (the next sequential migration after 026). The original
-- request said 024, but 024 (holdings_alerts) is already applied -- so this
-- follows the real sequence.
--
-- Stores the quantitative peer comparison (Part A), industry health
-- (Part B) and the LLM-generated, fact-checked narrative (Part C) for each
-- A-grade pick. peer_set_id links back to peer_sets row.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.peer_industry_analysis (
  id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  target_ticker               text NOT NULL,
  snapshot_date               date NOT NULL DEFAULT current_date,
  run_type                    text NOT NULL DEFAULT 'midday',
  peer_set_id                 bigint REFERENCES public.peer_sets,

  -- Part A: quantitative peer comparison
  peer_comparison             jsonb DEFAULT '{}'::jsonb,    -- full metric matrix incl. percentile ranks
  key_deltas                  jsonb DEFAULT '[]'::jsonb,    -- [{metric, target_value, peer_median, delta_pct, rank}, ...]
  overall_peer_score          numeric,                       -- 0..100
  within_industry_rank        integer,
  peer_count                  integer,
  best_in_industry_ticker     text,
  best_in_industry_is_target  boolean,

  -- Part B: industry health
  industry_metrics            jsonb DEFAULT '{}'::jsonb,
  industry_cycle_position     text,                          -- early_cycle | mid_cycle | late_cycle | contraction | recovery
  investability_score         numeric,                       -- 0..100
  industry_stance             text,                          -- overweight | market_weight | underweight
  disruption_risk_score       numeric,                       -- 0..100

  -- Part C: narrative
  industry_stance_rationale   text,
  peer_ranking_rationale      text,
  differentiation_thesis      text,
  relative_bull_case          text,
  relative_bear_case          text,
  watchlist_peer              text,
  watchlist_peer_reasoning    text,

  -- Factcheck pass
  verification_score          numeric,                       -- 0..1, share of claims with valid [DATA REF]
  quality_grade               text,                          -- VERIFIED | PARTIALLY_VERIFIED | UNVERIFIED

  created_at                  timestamptz DEFAULT now(),
  UNIQUE (target_ticker, snapshot_date, run_type)
);

CREATE INDEX IF NOT EXISTS idx_peer_industry_analysis_date
  ON public.peer_industry_analysis (snapshot_date DESC, target_ticker);

ALTER TABLE public.peer_industry_analysis ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.peer_industry_analysis
  FOR SELECT TO authenticated USING (true);
