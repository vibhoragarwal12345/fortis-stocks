-- ============================================================
-- 006_ticker_sentiment_rollup.sql
-- Per-ticker sentiment rollups produced by the upgraded
-- pipeline/processors/sentiment_scorer.py
--
-- Numbered 006 (the next sequential migration after 005). The original
-- request said 007, but 001-005 are the only prior migrations -- a
-- numbering gap would break ordered migration runners, so this follows
-- the same renumbering convention used by 002-005.
--
-- Besides the new rollup table, this migration ALSO adds two columns the
-- scorer needs but that the original schema lacks:
--   * ticker_debates.finbert_aggregated_score -- the request asks the
--     scorer to fill this; migration 002 never created it.
--   * sec_filings.sentiment_score -- the request asks the scorer to fill
--     this; migration 001 only gave sec_filings a `description` column.
-- ============================================================

-- ── New columns on existing tables ────────────────────────────────────────────
ALTER TABLE public.ticker_debates
  ADD COLUMN IF NOT EXISTS finbert_aggregated_score numeric;  -- score of bull+bear text

ALTER TABLE public.sec_filings
  ADD COLUMN IF NOT EXISTS sentiment_score numeric;           -- score of filing description

-- ── Rollup table ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ticker_sentiment_rollup (
  id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                 text NOT NULL,
  snapshot_date          date NOT NULL DEFAULT current_date,
  run_type               text NOT NULL DEFAULT 'midday',   -- premarket | midday | close
  weighted_sentiment     numeric,        -- -1..+1, recency- & source-weighted
  sentiment_volume       integer,        -- article count in the window
  sentiment_velocity     numeric,        -- weighted_sentiment minus 7-day average
  bullish_article_count  integer,
  bearish_article_count  integer,
  top_3_bullish          jsonb DEFAULT '[]'::jsonb,   -- [{headline,score,source,url},...]
  top_3_bearish          jsonb DEFAULT '[]'::jsonb,
  source_breakdown       jsonb DEFAULT '{}'::jsonb,    -- {tier: {avg_sentiment,count}}
  created_at             timestamptz DEFAULT now(),
  UNIQUE (ticker, snapshot_date, run_type)             -- one rollup per ticker per run
);

CREATE INDEX IF NOT EXISTS idx_ticker_sentiment_rollup_date
  ON public.ticker_sentiment_rollup (snapshot_date DESC, weighted_sentiment DESC);

-- Row-Level Security: shared read-only table, same pattern as 001-005.
-- Pipeline writes via the service_role key, which bypasses RLS.
ALTER TABLE public.ticker_sentiment_rollup ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.ticker_sentiment_rollup
  FOR SELECT TO authenticated USING (true);
