-- ============================================================
-- 040_feedback_collection.sql -- Step 7.5 Feedback collector
--
-- dashboard_events       One row per meaningful interaction in the
--                        web UI (page open, expansion, click, thumbs
--                        up / down, watchlist add / remove). Idempotent
--                        against duplicate logs from React re-renders
--                        via a UNIQUE constraint on a 5-second bucket
--                        plus an md5 hash of event_data.
--
-- user_action_summary    Daily per-user rollup populated by the
--                        feedback_aggregator agent. Backs the engagement
--                        score and -- once 60+ days of data exist --
--                        the per-user personalization overlay.
--
-- email events live in email_delivery_log (migration 039) already; the
-- aggregator joins these tables, no copy is needed here.
--
-- Spec called this 039_feedback_collection.sql; 039 was taken by
-- email_delivery, so this is 040.
-- ============================================================


-- ── 1. dashboard_events ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.dashboard_events (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id       uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  tenant_id     uuid,
  event_type    text NOT NULL,
  event_data    jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at   timestamptz NOT NULL DEFAULT now(),

  -- Idempotency: identical (user, type, data) inside a 5-second window
  -- collapses to a single row. React StrictMode double-mounts, double-
  -- clicks, and the same effect firing on hot reload all become no-ops.
  occurred_at_bucket  timestamptz GENERATED ALWAYS AS
    (date_bin('5 seconds'::interval, occurred_at, 'epoch'::timestamptz)) STORED,
  event_data_hash     text GENERATED ALWAYS AS
    (md5(coalesce(event_data::text, ''))) STORED,

  UNIQUE (user_id, event_type, occurred_at_bucket, event_data_hash)
);

CREATE INDEX IF NOT EXISTS idx_dashboard_events_user_date
  ON public.dashboard_events (user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_dashboard_events_type_date
  ON public.dashboard_events (event_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_dashboard_events_tenant
  ON public.dashboard_events (tenant_id, occurred_at DESC);

ALTER TABLE public.dashboard_events ENABLE ROW LEVEL SECURITY;

-- Users can only see their own events. Admin reads go through the
-- service-role client (which bypasses RLS) inside the aggregator agent.
DROP POLICY IF EXISTS "owner_read"   ON public.dashboard_events;
DROP POLICY IF EXISTS "owner_insert" ON public.dashboard_events;
CREATE POLICY "owner_read" ON public.dashboard_events
  FOR SELECT TO authenticated USING (user_id = auth.uid());
CREATE POLICY "owner_insert" ON public.dashboard_events
  FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());

COMMENT ON COLUMN public.dashboard_events.event_type IS
  'Enum-by-convention. See src/lib/track.ts EventType for the canonical '
  'list; the column is text so future event types do not require a '
  'migration.';
COMMENT ON COLUMN public.dashboard_events.occurred_at_bucket IS
  '5-second time bucket used by the dedup UNIQUE constraint. date_bin '
  'requires PostgreSQL 14+; Supabase runs PG15.';


-- ── 2. user_action_summary ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.user_action_summary (
  user_id           uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  summary_date      date NOT NULL,
  tenant_id         uuid,

  reports_opened                    int     DEFAULT 0,
  picks_clicked                     int     DEFAULT 0,
  avg_picks_clicked_per_report      numeric DEFAULT 0,
  thesis_expansions                 int     DEFAULT 0,
  quant_expansions                  int     DEFAULT 0,
  smart_money_expansions            int     DEFAULT 0,
  peer_expansions                   int     DEFAULT 0,
  positions_acknowledged            int     DEFAULT 0,
  explicit_feedback_count           int     DEFAULT 0,   -- thumbs up + down
  watchlist_changes                 int     DEFAULT 0,   -- sub_added + unsub_added
  ticker_searches                   int     DEFAULT 0,

  -- Top sectors (by clicks into picks in those sectors) and per-signal
  -- click rates. Shape:
  --   preferred_sectors:      [{sector: "Tech", clicks: 12}, ...]
  --   preferred_signal_types: {"news": 0.62, "options": 0.31, ...}
  preferred_sectors          jsonb DEFAULT '[]'::jsonb,
  preferred_signal_types     jsonb DEFAULT '{}'::jsonb,

  -- 0-100 composite. Formula lives in feedback_aggregator.py so it can
  -- evolve without a schema migration.
  engagement_score  numeric DEFAULT 0,

  created_at        timestamptz DEFAULT now(),
  updated_at        timestamptz DEFAULT now(),

  PRIMARY KEY (user_id, summary_date)
);

CREATE INDEX IF NOT EXISTS idx_user_summary_date
  ON public.user_action_summary (summary_date DESC, engagement_score DESC);
CREATE INDEX IF NOT EXISTS idx_user_summary_tenant
  ON public.user_action_summary (tenant_id, summary_date DESC);

ALTER TABLE public.user_action_summary ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "owner_read" ON public.user_action_summary;
CREATE POLICY "owner_read" ON public.user_action_summary
  FOR SELECT TO authenticated USING (user_id = auth.uid());

COMMENT ON TABLE public.user_action_summary IS
  'Daily per-user engagement rollup. Written by '
  'pipeline/agents/feedback_aggregator.py via the service-role client. '
  'The personalization overlay (per-user signal weights) is gated on '
  '60+ days of rows existing for the user.';
