-- ============================================================
-- 008_pattern_matches.sql
-- Daily historical-analog output produced by
-- pipeline/agents/pattern_matcher.py
--
-- Numbered 008 (the next sequential migration after 007).
--
-- NOTE ON ARCHITECTURE
--   The original request asked for a pgvector `historical_patterns` table.
--   A full S&P-1500 x 5-year fingerprint store is ~1.9M rows of 384-dim
--   vectors (~4-7 GB) -- well past Supabase's 500 MB free-tier cap. By
--   decision, the embedding store now lives in a LOCAL FAISS index
--   (pipeline/data/historical_patterns.faiss + .parquet), so there is no
--   historical_patterns table and no pgvector extension here. Only the
--   small daily pattern_matches output is persisted to Supabase.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.pattern_matches (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker              text NOT NULL,
  snapshot_date       date NOT NULL DEFAULT current_date,
  matched_dates       jsonb DEFAULT '[]'::jsonb,   -- 10 closest (ticker,date,similarity)
  avg_1d_return       numeric,
  avg_5d_return       numeric,
  avg_20d_return      numeric,
  avg_60d_return      numeric,
  win_rate_5d         numeric,                     -- 0..1
  win_rate_20d        numeric,                     -- 0..1
  sample_analog_text  text,
  confidence          numeric,                     -- neighbour-cluster tightness, 0..100
  created_at          timestamptz DEFAULT now(),
  UNIQUE (ticker, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_pattern_matches_date
  ON public.pattern_matches (snapshot_date DESC, avg_5d_return DESC);

-- Row-Level Security: shared read-only table, same pattern as 001-007.
-- Pipeline writes via the service_role key, which bypasses RLS.
ALTER TABLE public.pattern_matches ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.pattern_matches
  FOR SELECT TO authenticated USING (true);
