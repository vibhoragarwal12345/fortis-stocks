-- ============================================================
-- 036_audio_transcripts.sql
-- Tier-4 transcript fallback: audio transcription of earnings-call webcasts.
--
-- Companies publicly webcast their earnings calls. When neither the SEC 8-K
-- exhibits (Tier 1/2) nor the company IR RSS feed (Tier 3) yield a usable
-- transcript, audio_transcriber.py downloads the webcast audio and transcribes
-- it locally with whisper (open-source, runs offline, no API cost).
--
-- This table is the permanent, audio-specific record (audio URL, size,
-- transcription method, timing, whisper confidence). The standard transcript
-- copy is ALSO written to earnings_transcripts with source_path =
-- 'audio_transcription', so the orchestrator's existing cache still applies.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.audio_transcripts (
  id                              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                          text NOT NULL,
  fiscal_year                     int  NOT NULL,
  fiscal_quarter                  int  NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),

  earnings_date                   date,            -- when the call took place (best-effort)
  audio_url                       text,            -- source webcast/audio URL
  audio_size_mb                   numeric,         -- downloaded file size

  transcription_method            text,            -- 'whisper_cpp_medium_en' | 'whisper_medium_en'
  raw_transcript                  text,            -- whisper output, verbatim (no words changed)
  structured_segments             jsonb,           -- [{role, name, text, segment_type}] after speaker attribution

  transcription_quality_estimate  numeric,         -- 0..1, mean whisper segment/token confidence
  transcription_time_seconds      numeric,         -- wall-clock transcription time

  fetched_at                      timestamptz DEFAULT now(),  -- when the audio was downloaded
  transcribed_at                  timestamptz,                -- when transcription finished

  UNIQUE (ticker, fiscal_year, fiscal_quarter)
);

CREATE INDEX IF NOT EXISTS idx_audio_transcripts_ticker
  ON public.audio_transcripts (ticker, fiscal_year DESC, fiscal_quarter DESC);
CREATE INDEX IF NOT EXISTS idx_audio_transcripts_date
  ON public.audio_transcripts (earnings_date DESC NULLS LAST);

ALTER TABLE public.audio_transcripts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "authenticated_read" ON public.audio_transcripts;
CREATE POLICY "authenticated_read" ON public.audio_transcripts
  FOR SELECT TO authenticated USING (true);

COMMENT ON TABLE public.audio_transcripts IS
  'Tier-4 transcript fallback. Earnings-call webcast audio transcribed locally '
  'with whisper (medium.en). The standard transcript copy is also written to '
  'earnings_transcripts (source_path = audio_transcription).';

COMMENT ON COLUMN public.audio_transcripts.transcription_quality_estimate IS
  'Mean whisper confidence (0..1) across segments/tokens. This is an audio-'
  'quality estimate, distinct from earnings_transcripts.quality_grade which '
  'grades transcript completeness (full_call / partial / ...).';

COMMENT ON COLUMN public.audio_transcripts.raw_transcript IS
  'Verbatim whisper output. Speaker attribution is layered on separately in '
  'structured_segments; the raw words are never altered.';
