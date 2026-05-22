-- ============================================================
-- 034_earnings_transcripts.sql
-- Step 6.7 transcript-source upgrade. Stores full earnings call transcripts
-- pulled from SEC EDGAR 8-K exhibits (free, authoritative, permanent).
--
-- Spec called for 029 but that's taken (news_resolution_status); using 034
-- as the next sequential slot.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.earnings_transcripts (
  id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                text NOT NULL,
  fiscal_year           int  NOT NULL,
  fiscal_quarter        int  NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),

  earnings_date         date,            -- when the call actually happened
  transcript_text       text,            -- full body, cleaned
  structured_segments   jsonb,           -- [{role, name, text, segment_type}]

  quality_grade         text NOT NULL,   -- full_call | partial | press_release_only | unavailable
  source_url            text,            -- direct link to SEC filing/exhibit
  source_path           text,            -- which PATH succeeded: sec_8k_item202 | sec_8k_item701 | company_ir | press_release_fallback

  fetched_at            timestamptz DEFAULT now(),

  UNIQUE (ticker, fiscal_year, fiscal_quarter)
);

CREATE INDEX IF NOT EXISTS idx_earnings_transcripts_ticker
  ON public.earnings_transcripts (ticker, fiscal_year DESC, fiscal_quarter DESC);
CREATE INDEX IF NOT EXISTS idx_earnings_transcripts_date
  ON public.earnings_transcripts (earnings_date DESC NULLS LAST);

ALTER TABLE public.earnings_transcripts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "authenticated_read" ON public.earnings_transcripts;
CREATE POLICY "authenticated_read" ON public.earnings_transcripts
  FOR SELECT TO authenticated USING (true);

COMMENT ON TABLE public.earnings_transcripts IS
  'Full earnings call transcripts pulled from SEC EDGAR 8-K exhibits '
  '(Item 2.02 / Item 7.01). quality_grade drives downstream earnings '
  'signal strength in smart_money_intel.';

COMMENT ON COLUMN public.earnings_transcripts.quality_grade IS
  'full_call (complete with Q&A, > 3000 words, > 5 Q&A pairs) | partial '
  '(prepared remarks only) | press_release_only (no transcript found, fell '
  'back to 8-K Item 2.02 description) | unavailable (nothing usable).';

COMMENT ON COLUMN public.earnings_transcripts.source_path IS
  'sec_8k_item202 (transcript in Item 2.02 exhibit -- rare but happens) | '
  'sec_8k_item701 (transcript in Item 7.01 Reg-FD filing -- common path) | '
  'company_ir (IR page scrape -- fragile) | press_release_fallback '
  '(no transcript found, press release description only).';
