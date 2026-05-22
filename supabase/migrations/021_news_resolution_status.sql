-- ============================================================
-- 021_news_resolution_status.sql
-- Tracks whether a news_item's ticker assignment passes verification.
--
-- Numbered 021 (the next sequential migration after 020).
--
-- news_resolver.py re-checks the source-tagged ticker against the
-- ticker_master company-name aliases (and rejects foreign-exchange
-- context). Failing rows have sentiment_score nulled AND get
-- resolution_status='unresolved' so a future sentiment_scorer run
-- doesn't simply re-score the same bad row.
-- ============================================================

ALTER TABLE public.news_items
  ADD COLUMN IF NOT EXISTS resolution_status text;

COMMENT ON COLUMN public.news_items.resolution_status IS
  'NULL = source-tagged ticker trusted; "unresolved" = ticker verification '
  'failed (no alias match OR foreign-exchange mismatch); these rows are '
  'excluded from the sentiment rollup.';

CREATE INDEX IF NOT EXISTS idx_news_items_resolution_status
  ON public.news_items (resolution_status);
