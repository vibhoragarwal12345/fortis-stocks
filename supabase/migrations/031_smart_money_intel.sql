-- ============================================================
-- 031_smart_money_intel.sql
-- Smart Money Intelligence (Step 6.7) -- one row per (ticker, snapshot_date,
-- run_type) holding the four institutional signals (earnings call, insider,
-- short interest, analyst revisions), the synthesised pattern, the composite
-- score, the Groq intel note, and the factcheck verification result.
--
-- Spec called for "028_smart_money_intel.sql" but 028 is already taken
-- (auto_added_watchlist); using 031 as the next sequential slot.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.smart_money_intel (
  id                              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                          text  NOT NULL,
  snapshot_date                   date  NOT NULL,
  run_type                        text  NOT NULL,   -- premarket | midday | close

  -- ── earnings call sub-signal ────────────────────────────────────────────────
  earnings_signal_score           numeric,
  earnings_signal_strength        numeric,
  earnings_last_report_date       date,
  earnings_verdict                text,
  earnings_tone_summary           jsonb,
  earnings_transcript_quality     text,             -- full | partial | press_release_only | none

  -- ── insider activity sub-signal ─────────────────────────────────────────────
  insider_signal_score            numeric,
  insider_signal_strength         numeric,
  insider_detected_cluster        text,             -- STRONG_INSIDER_BUYING / SELLING / ...
  insider_recent_transactions     jsonb,
  insider_10b5_1_count            int DEFAULT 0,
  insider_discretionary_count     int DEFAULT 0,
  insider_post_earnings_summary   text,

  -- ── short interest sub-signal ───────────────────────────────────────────────
  short_signal_score              numeric,
  short_signal_strength           numeric,
  short_pct_of_float              numeric,
  days_to_cover                   numeric,
  squeeze_potential_score         numeric,
  short_trend_direction           text,             -- building | covering | stable | unknown

  -- ── analyst revisions sub-signal ────────────────────────────────────────────
  revision_signal_score           numeric,
  revision_signal_strength        numeric,
  revision_velocity               text,             -- STRONG_POSITIVE / NEGATIVE / NEUTRAL / cluster
  net_revision_count              int,

  -- ── enrichment ──────────────────────────────────────────────────────────────
  congressional_activity          jsonb,            -- recent Senate/House trades for this ticker

  -- ── synthesis layer ─────────────────────────────────────────────────────────
  pattern_detected                text,             -- FULL_CONVICTION_LONG / INSIDER_SMOKE_SIGNAL / ...
  composite_smart_money_score     numeric,          -- -100 .. +100
  confidence_level                text,             -- HIGH | MEDIUM | LOW

  -- ── LLM-generated intel note + verification ─────────────────────────────────
  intel_note                      text,
  verification_score              numeric,
  quality_grade                   text,             -- VERIFIED | PARTIALLY_VERIFIED | UNVERIFIED

  created_at                      timestamptz DEFAULT now(),

  UNIQUE (ticker, snapshot_date, run_type)
);

CREATE INDEX IF NOT EXISTS idx_smart_money_intel_ticker
  ON public.smart_money_intel (ticker, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_smart_money_intel_pattern
  ON public.smart_money_intel (pattern_detected, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_smart_money_intel_date
  ON public.smart_money_intel (snapshot_date DESC, run_type);

ALTER TABLE public.smart_money_intel ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "authenticated_read" ON public.smart_money_intel;
CREATE POLICY "authenticated_read" ON public.smart_money_intel
  FOR SELECT TO authenticated USING (true);

COMMENT ON TABLE public.smart_money_intel IS
  'Step 6.7 -- Smart Money Intelligence. Triangulates four institutional '
  'signals (earnings call, insider, short interest, analyst revisions) into a '
  'pattern classification + composite score. Powers the differentiating '
  '"Smart Money Verdict" section. LOW confidence rows are informational only '
  'and do not affect conviction grade.';

COMMENT ON COLUMN public.smart_money_intel.composite_smart_money_score IS
  '-100 to +100. Computed per-pattern (full-conviction = equal weights; '
  'insider-smoke-signal weights insider at 0.5 because that is the highest '
  'information-advantage signal when public narrative diverges).';

COMMENT ON COLUMN public.smart_money_intel.confidence_level IS
  'HIGH = 4/4 sub-signals with strength > 50. MEDIUM = 2-3/4. LOW = 0-1/4. '
  'LOW patterns cannot drive conviction grade changes per spec.';
