-- ============================================================
-- 029_pick_outcomes.sql
-- Performance-tracking schema for Step 6.6 (Backtest Engine).
--
-- Three tables:
--   pick_outcomes              -- one row per (run_date, run_type, ticker) for
--                                 every A/B/C pick; entry_price + forward prices
--                                 + returns / alpha vs SPY at 1d / 5d / 20d / 60d.
--   system_performance_rollup  -- aggregated metrics by (rollup_date, run_type,
--                                 conviction_grade) for the track-record page.
--   agent_attribution          -- per-agent contribution to winners vs losers,
--                                 plus signal-strength <-> forward-return
--                                 predictive power (Spearman-style).
--
-- Numbered 029 (the next sequential migration after 028). Purely additive.
-- ============================================================


-- ── 1. pick_outcomes ──────────────────────────────────────────────────────────
--    One row per (run_date, run_type, ticker) -- written by outcome_tracker
--    at the moment of the pick (entry_price), then progressively filled in
--    by update_outcomes() as trading days elapse.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.pick_outcomes (
  id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                      text NOT NULL,
  recommended_date            date NOT NULL,
  recommended_run_type        text NOT NULL,   -- premarket | midday | close
  conviction_grade            text NOT NULL,   -- 'A' | 'B' | 'C'
  composite_score             numeric,         -- final score that produced the grade
  validated_strength          numeric,         -- cross-validator score at pick time

  entry_price                 numeric,         -- close on recommended_date

  price_1d                    numeric,
  price_5d                    numeric,
  price_20d                   numeric,
  price_60d                   numeric,

  return_1d                   numeric,         -- percent
  return_5d                   numeric,
  return_20d                  numeric,
  return_60d                  numeric,

  benchmark_return_1d         numeric,         -- SPY over the same window
  benchmark_return_5d         numeric,
  benchmark_return_20d        numeric,
  benchmark_return_60d        numeric,

  alpha_1d                    numeric,         -- return - benchmark_return
  alpha_5d                    numeric,
  alpha_20d                   numeric,
  alpha_60d                   numeric,

  max_drawdown_during_period  numeric,         -- worst close-to-close drop (percent)
  max_gain_during_period      numeric,         -- best gain at any point (percent)

  still_active                boolean NOT NULL DEFAULT TRUE,
  last_updated                timestamptz DEFAULT now(),
  created_at                  timestamptz DEFAULT now(),

  UNIQUE (ticker, recommended_date, recommended_run_type)
);

CREATE INDEX IF NOT EXISTS idx_pick_outcomes_date
  ON public.pick_outcomes (recommended_date DESC);
CREATE INDEX IF NOT EXISTS idx_pick_outcomes_active
  ON public.pick_outcomes (still_active) WHERE still_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_pick_outcomes_grade
  ON public.pick_outcomes (conviction_grade, recommended_date DESC);


-- ── 2. system_performance_rollup ──────────────────────────────────────────────
--    Aggregated performance by (rollup_date, run_type, conviction_grade).
--    Written by outcome_tracker.compute_rollups() (weekly cadence).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.system_performance_rollup (
  id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  rollup_date              date NOT NULL,
  run_type                 text NOT NULL,         -- premarket | midday | close | 'all'
  conviction_grade         text NOT NULL,         -- 'A' | 'B' | 'C' | 'all'

  pick_count               int NOT NULL DEFAULT 0,

  avg_return_1d            numeric,
  avg_return_5d            numeric,
  avg_return_20d           numeric,
  avg_return_60d           numeric,

  avg_alpha_1d             numeric,
  avg_alpha_5d             numeric,
  avg_alpha_20d            numeric,
  avg_alpha_60d            numeric,

  win_rate_1d              numeric,               -- % of picks with return > 0
  win_rate_5d              numeric,
  win_rate_20d             numeric,
  win_rate_60d             numeric,

  alpha_win_rate_5d        numeric,               -- % of picks beating SPY
  alpha_win_rate_20d       numeric,

  sharpe_ratio_estimated   numeric,               -- mean / stdev of alpha_20d, annualized
  t_statistic_alpha_20d    numeric,               -- t-test of alpha_20d vs 0
  is_statistically_significant boolean,           -- |t| >= 1.5 AND sample >= 10

  max_drawdown_avg         numeric,
  max_gain_avg             numeric,

  best_performing_pick     jsonb,                 -- {ticker, return_20d, recommended_date}
  worst_performing_pick    jsonb,

  sample_disclaimer        text,                  -- "insufficient sample", "building", etc.
  created_at               timestamptz DEFAULT now(),

  UNIQUE (rollup_date, run_type, conviction_grade)
);

CREATE INDEX IF NOT EXISTS idx_system_performance_rollup_date
  ON public.system_performance_rollup (rollup_date DESC);


-- ── 3. agent_attribution ──────────────────────────────────────────────────────
--    For each agent / signal source, the relationship between signal strength
--    and forward outcome -- "when this signal was strong, how often was the
--    pick right? what was the correlation with 20-day forward return?"
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.agent_attribution (
  id                              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_date                   date NOT NULL,
  agent_name                      text NOT NULL,

  -- Sample sizes
  sample_size                     int NOT NULL DEFAULT 0,
  strong_signal_sample_size       int NOT NULL DEFAULT 0,    -- signal in top quartile

  -- Win rates conditional on strong signal
  contribution_to_correct_picks   numeric,   -- when signal strong, % of picks with alpha_20d > 0
  contribution_to_wrong_picks     numeric,   -- when signal strong, % of picks with alpha_20d <= 0

  -- Predictive power
  signal_predictive_power         numeric,   -- correlation (signal strength, forward 20d return)

  notes                           text,
  created_at                      timestamptz DEFAULT now(),

  UNIQUE (snapshot_date, agent_name)
);

CREATE INDEX IF NOT EXISTS idx_agent_attribution_date
  ON public.agent_attribution (snapshot_date DESC);


-- ═════════════════════════════════════════════════════════════════════════════
-- Row-Level Security: shared read-only tables, same pattern as 001-028.
-- Pipeline writes via the service_role key, which bypasses RLS.
-- ═════════════════════════════════════════════════════════════════════════════
ALTER TABLE public.pick_outcomes              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.system_performance_rollup  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_attribution          ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.pick_outcomes
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "authenticated_read" ON public.system_performance_rollup
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "authenticated_read" ON public.agent_attribution
  FOR SELECT TO authenticated USING (true);


COMMENT ON TABLE public.pick_outcomes IS
  'Forward-return tracking for every A/B/C pick produced by the pipeline. '
  'Entry price written at pick time; forward prices and alpha vs SPY filled in '
  'progressively by outcome_tracker.update_outcomes() until 60d matures. '
  'still_active=FALSE means 60d window completed; row is frozen.';

COMMENT ON TABLE public.system_performance_rollup IS
  'Aggregated track-record metrics. is_statistically_significant guards against '
  'small-sample over-celebration (|t| >= 1.5 AND sample >= 10). sample_disclaimer '
  'is a human-readable status when the sample is too small to draw conclusions.';

COMMENT ON TABLE public.agent_attribution IS
  'Per-agent contribution: when this agent emits a strong signal, how often '
  'does the resulting pick beat SPY? signal_predictive_power is the correlation '
  'between signal strength and forward 20-day return -- this feeds Tier 5 '
  'weight learning, but is read-only / advisory for now.';
