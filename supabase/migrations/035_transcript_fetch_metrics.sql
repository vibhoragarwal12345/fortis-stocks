-- ============================================================
-- 035_transcript_fetch_metrics.sql
-- One row per transcript fetch attempt -- powers coverage monitoring and
-- helps identify companies that consistently don't file transcripts.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.transcript_fetch_metrics (
  id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                text NOT NULL,
  fiscal_year           int,
  fiscal_quarter        int,
  attempted_at          timestamptz DEFAULT now(),

  success               boolean NOT NULL DEFAULT FALSE,
  final_quality_grade   text,
  path_succeeded        text,
  attempts_made         jsonb,           -- [{path, success, error_if_any, exhibit_url}]
  total_time_seconds    numeric,

  rate_limit_hits       int DEFAULT 0,   -- 429 count for this attempt

  created_at            timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tfm_ticker
  ON public.transcript_fetch_metrics (ticker, attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_tfm_quality
  ON public.transcript_fetch_metrics (final_quality_grade, attempted_at DESC);

ALTER TABLE public.transcript_fetch_metrics ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "authenticated_read" ON public.transcript_fetch_metrics;
CREATE POLICY "authenticated_read" ON public.transcript_fetch_metrics
  FOR SELECT TO authenticated USING (true);

COMMENT ON TABLE public.transcript_fetch_metrics IS
  'Operational metrics for the SEC transcript fetcher. After 30 days of '
  'runs, query this to see (a) overall full_call coverage rate, (b) which '
  'tickers consistently lack transcripts (mark them press_release_only '
  'permanently), and (c) rate-limit hits for monitoring.';
