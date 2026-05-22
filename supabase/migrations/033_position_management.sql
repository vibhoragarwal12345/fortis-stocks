-- ============================================================
-- 033_position_management.sql
-- Position Management & Exit Signals (Step 6.8).
--
-- Two tables:
--   position_signals                -- one row per (portfolio_id, ticker,
--                                      snapshot_date, run_type) capturing
--                                      thesis_degradation / profit_taking /
--                                      stop_loss / time_review /
--                                      better_alternative / no_action.
--   portfolio_rebalance_suggestions -- portfolio-level aggregation; overweight /
--                                      underweight / concentration / factor
--                                      drift alerts + top-3 priorities.
--
-- Spec called for "027_position_management.sql" but 027 is taken
-- (peer_industry_analysis); using 033 as the next sequential slot.
--
-- ETHICAL FRAMING (enforced in the agent's prompt + forbidden-word scan):
--   This is RESEARCH for a licensed advisor, not advice to a retail investor.
--   Every text field must use "review", "consider", "thesis weakening" --
--   never "sell", "exit", "must", "recommend you".
-- ============================================================


-- ── 1. position_signals ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.position_signals (
  id                            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  portfolio_id                  uuid NOT NULL REFERENCES public.portfolios(id) ON DELETE CASCADE,
  ticker                        text NOT NULL,
  snapshot_date                 date NOT NULL,
  run_type                      text NOT NULL,           -- premarket | midday | close

  -- Position snapshot at signal time
  entry_basis                   jsonb,                   -- {entry_grade, entry_thesis, entry_catalyst, entry_date}
  current_price                 numeric,
  cost_basis                    numeric,
  unrealized_return_pct         numeric,
  holding_period_days           int,

  -- Signal classification
  signal_type                   text NOT NULL,           -- thesis_degradation | profit_taking | stop_loss | time_review | better_alternative | no_action
  signal_strength               numeric CHECK (signal_strength BETWEEN 0 AND 100),
  recommended_action            text,                    -- review_position | consider_trim | consider_exit | add_on_weakness | hold
  recommended_size_change_pct   numeric,                 -- negative = trim, positive = add
  reasoning                     text,
  supporting_data               jsonb,

  requires_immediate_attention  boolean NOT NULL DEFAULT FALSE,
  reviewed_at                   timestamptz,             -- set when advisor clicks the "Mark reviewed" link
  reviewed_by                   uuid REFERENCES auth.users(id),

  created_at                    timestamptz DEFAULT now(),

  UNIQUE (portfolio_id, ticker, snapshot_date, run_type, signal_type)
);

CREATE INDEX IF NOT EXISTS idx_position_signals_portfolio
  ON public.position_signals (portfolio_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_position_signals_attention
  ON public.position_signals (requires_immediate_attention, snapshot_date DESC)
  WHERE requires_immediate_attention = TRUE;
CREATE INDEX IF NOT EXISTS idx_position_signals_ticker
  ON public.position_signals (ticker, snapshot_date DESC);


-- ── 2. portfolio_rebalance_suggestions ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.portfolio_rebalance_suggestions (
  id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  portfolio_id                uuid NOT NULL REFERENCES public.portfolios(id) ON DELETE CASCADE,
  snapshot_date               date NOT NULL,
  run_type                    text NOT NULL,

  current_total_value         numeric,
  overweight_positions        jsonb,           -- [{ticker, weight, target_weight, excess_pct}]
  underweight_positions       jsonb,
  concentration_alerts        jsonb,           -- [{type, scope, severity, description}]
  factor_drift_alerts         jsonb,
  cash_deployment_opportunity boolean,
  top_3_priorities            jsonb,           -- [{action, ticker, reasoning}]
  reasoning                   text,            -- LLM-generated portfolio-level summary

  created_at                  timestamptz DEFAULT now(),

  UNIQUE (portfolio_id, snapshot_date, run_type)
);

CREATE INDEX IF NOT EXISTS idx_rebalance_portfolio
  ON public.portfolio_rebalance_suggestions (portfolio_id, snapshot_date DESC);


-- ═════════════════════════════════════════════════════════════════════════════
-- Row-Level Security: owner-only, derived via portfolios join (same pattern
-- as portfolio_holdings in migration 001).
-- ═════════════════════════════════════════════════════════════════════════════
ALTER TABLE public.position_signals                ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_rebalance_suggestions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "owner_all" ON public.position_signals;
CREATE POLICY "owner_all" ON public.position_signals
  USING (
    portfolio_id IN (SELECT id FROM public.portfolios WHERE user_id = auth.uid())
  )
  WITH CHECK (
    portfolio_id IN (SELECT id FROM public.portfolios WHERE user_id = auth.uid())
  );

DROP POLICY IF EXISTS "owner_all" ON public.portfolio_rebalance_suggestions;
CREATE POLICY "owner_all" ON public.portfolio_rebalance_suggestions
  USING (
    portfolio_id IN (SELECT id FROM public.portfolios WHERE user_id = auth.uid())
  )
  WITH CHECK (
    portfolio_id IN (SELECT id FROM public.portfolios WHERE user_id = auth.uid())
  );


COMMENT ON TABLE public.position_signals IS
  'Step 6.8 -- per-position exit / review signals. requires_immediate_attention '
  'triggers [URGENT REVIEW] in email subjects and the top-of-report alert '
  'banner. All reasoning text is research-framed; the agent runs a '
  'forbidden-word scan (buy/sell/must/recommend) before write.';

COMMENT ON TABLE public.portfolio_rebalance_suggestions IS
  'Portfolio-level aggregation of overweight/underweight/concentration/factor-'
  'drift alerts + LLM-generated top-3 priorities. Surfaces at the TOP of every '
  'report so advisors see "protect existing capital" before "explore new ideas".';
