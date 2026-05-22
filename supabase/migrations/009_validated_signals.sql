-- ============================================================
-- 009_validated_signals.sql
-- Cross-source signal validation produced by
-- pipeline/agents/cross_validator.py
--
-- Numbered 009 (the next sequential migration after 008). The original
-- request said 010, but 001-008 are the only prior migrations -- a
-- numbering gap would break ordered migration runners, so this follows
-- the same renumbering convention used by 002-006.
--
-- A signal counts as "validated" only when independent source categories
-- confirm it (the triangulation principle). validated_strength is the
-- final score the ranking engine should consume.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.validated_signals (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker              text NOT NULL,
  snapshot_time       timestamptz NOT NULL DEFAULT now(),
  run_type            text NOT NULL DEFAULT 'midday',   -- premarket | midday | close
  base_score          numeric,                          -- weighted sum of raw signals
  confirmation_count  int CHECK (confirmation_count BETWEEN 0 AND 6),
  direction_alignment numeric,                           -- 0..1
  confirmation_bonus  numeric,
  direction_bonus     numeric,
  contradiction_flag  boolean DEFAULT false,
  validated_strength  numeric,                           -- final ranking score
  category_signals    jsonb DEFAULT '{}'::jsonb,         -- per-category breakdown + direction
  created_at          timestamptz DEFAULT now(),
  UNIQUE (ticker, snapshot_time, run_type)
);

CREATE INDEX IF NOT EXISTS idx_validated_signals_strength
  ON public.validated_signals (snapshot_time DESC, validated_strength DESC);
CREATE INDEX IF NOT EXISTS idx_validated_signals_ticker
  ON public.validated_signals (ticker, snapshot_time DESC);

-- Row-Level Security: shared read-only table, same pattern as 001-008.
-- Pipeline writes via the service_role key, which bypasses RLS.
ALTER TABLE public.validated_signals ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.validated_signals
  FOR SELECT TO authenticated USING (true);
