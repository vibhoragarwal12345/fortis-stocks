-- ============================================================
-- 004_options_signals.sql
-- Institutional options-activity signals produced by
-- pipeline/agents/options_flow.py
--
-- Numbered 004 (the next sequential migration after 003_macro_context).
-- The original request said 005, but 001-003 are the only prior
-- migrations -- a numbering gap would break ordered migration runners,
-- so this follows the same renumbering convention used by 002 and 003.
--
-- Grain: one row per ticker per snapshot. expiration_date holds the
-- front (nearest) expiration scanned; volume / unusual-strike fields
-- aggregate across all scanned expirations for that ticker.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.options_signals (
  id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                text NOT NULL,
  snapshot_time         timestamptz NOT NULL DEFAULT now(),
  expiration_date       date,                       -- front expiration scanned
  total_call_volume     bigint,
  total_put_volume      bigint,
  put_call_ratio        numeric,
  unusual_call_strikes  jsonb DEFAULT '[]'::jsonb,  -- [{exp,strike,volume,oi,iv},...]
  unusual_put_strikes   jsonb DEFAULT '[]'::jsonb,
  large_block_alerts    jsonb DEFAULT '[]'::jsonb,  -- single strikes >5000 contracts
  iv_percentile         numeric,                    -- 0..100, cross-sectional proxy
  iv_skew               numeric,                    -- OTM put IV - OTM call IV
  pattern_detected      text DEFAULT 'none',        -- earnings_straddle | call_speculation | put_hedging | none
  conviction_score      numeric CHECK (conviction_score BETWEEN 0 AND 100),
  created_at            timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_options_signals_snapshot
  ON public.options_signals (snapshot_time DESC, conviction_score DESC);

CREATE INDEX IF NOT EXISTS idx_options_signals_ticker
  ON public.options_signals (ticker, snapshot_time DESC);

-- Row-Level Security: shared read-only table, same pattern as 001-003.
-- Pipeline writes via the service_role key, which bypasses RLS.
ALTER TABLE public.options_signals ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.options_signals
  FOR SELECT TO authenticated USING (true);
