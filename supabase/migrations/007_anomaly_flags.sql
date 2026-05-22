-- ============================================================
-- 007_anomaly_flags.sql
-- Statistical anomaly flags produced by
-- pipeline/agents/anomaly_detector.py
--
-- Numbered 007 -- the next sequential migration after 006. (This time
-- the request's number already matched the sequence; no renumber.)
--
-- Grain: one row per (ticker, run, anomaly type). A single run shares
-- one snapshot_time across all its rows.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.anomaly_flags (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker         text NOT NULL,
  snapshot_time  timestamptz NOT NULL DEFAULT now(),
  run_type       text NOT NULL DEFAULT 'midday',   -- premarket | midday | close
  flag_type      text NOT NULL,                    -- e.g. gap_anomaly, volume_spike
  flag_value     numeric,                          -- the raw value behind the flag
  severity       numeric CHECK (severity BETWEEN 0 AND 100),
  context        jsonb DEFAULT '{}'::jsonb,        -- supporting data for the flag
  created_at     timestamptz DEFAULT now(),
  UNIQUE (ticker, snapshot_time, flag_type)         -- one flag per type per run
);

CREATE INDEX IF NOT EXISTS idx_anomaly_flags_snapshot
  ON public.anomaly_flags (snapshot_time DESC, severity DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_flags_ticker
  ON public.anomaly_flags (ticker, snapshot_time DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_flags_type
  ON public.anomaly_flags (flag_type);

-- Row-Level Security: shared read-only table, same pattern as 001-006.
-- Pipeline writes via the service_role key, which bypasses RLS.
ALTER TABLE public.anomaly_flags ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.anomaly_flags
  FOR SELECT TO authenticated USING (true);
