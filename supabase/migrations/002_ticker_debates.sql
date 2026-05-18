-- ============================================================
-- 002_ticker_debates.sql
-- Crowd-intelligence debate summaries produced by
-- pipeline/agents/crowd_intelligence.py
--
-- Numbered 002 (the next sequential migration after 001). The original
-- request said 003, but 001_initial_schema.sql is the only prior migration
-- -- a numbering gap would break ordered migration runners.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.ticker_debates (
  id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                text NOT NULL,
  snapshot_date         date NOT NULL DEFAULT current_date,
  run_type              text NOT NULL DEFAULT 'midday',  -- premarket | midday | close
  attention_score       numeric,        -- 0..100 composite crowd-attention
  sentiment_balance     numeric,        -- -100..+100 (near 0 = active debate)
  bull_case             text,
  bear_case             text,
  consensus_tilt        text,
  retail_pro_divergence text,
  raw_signals           jsonb,          -- full normalized per-source data
  created_at            timestamptz DEFAULT now(),
  UNIQUE (ticker, snapshot_date, run_type)   -- one debate per ticker per run
);

CREATE INDEX idx_ticker_debates_date_attention
  ON public.ticker_debates (snapshot_date DESC, attention_score DESC);

-- Row-Level Security: shared read-only table, same pattern as 001.
-- Pipeline writes via the service_role key, which bypasses RLS.
ALTER TABLE public.ticker_debates ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.ticker_debates
  FOR SELECT TO authenticated USING (true);
