-- ============================================================
-- 003_macro_context.sql
-- Daily macroeconomic context snapshots produced by
-- pipeline/agents/macro_agent.py
--
-- Numbered 003 (the next sequential migration after 002_ticker_debates).
-- The original request said 004, but 001 and 002 are the only prior
-- migrations -- a numbering gap would break ordered migration runners,
-- so this follows the same renumbering convention used by 002.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.macro_context (
  id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_time           timestamptz NOT NULL DEFAULT now(),
  economic_indicators     jsonb,         -- all FRED series + week/month deltas
  yield_curve             jsonb,         -- full Treasury.gov daily yield curve
  fed_calendar            jsonb,         -- Fed speeches / FOMC, next 14 days
  economic_calendar       jsonb,         -- this week's macro releases (Finnhub)
  earnings_calendar       jsonb,         -- S&P 1500 earnings, next 7 days
  global_indices          jsonb,         -- US + international index ETFs / indices
  crypto_snapshot         jsonb,         -- BTC / ETH close + 24h change
  sector_performance      jsonb,         -- 11 SPDR sector ETFs, 1d / 5d / 1mo
  regime_classification   text,          -- risk_on | risk_off | neutral | inflation_concern
  narrative_summary       text,          -- LLM-generated 3-paragraph macro wrap
  created_at              timestamptz DEFAULT now()
);

CREATE INDEX idx_macro_context_snapshot
  ON public.macro_context (snapshot_time DESC);

-- Row-Level Security: shared read-only table, same pattern as 001 / 002.
-- Pipeline writes via the service_role key, which bypasses RLS.
ALTER TABLE public.macro_context ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.macro_context
  FOR SELECT TO authenticated USING (true);
