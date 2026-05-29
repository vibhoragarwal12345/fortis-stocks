-- ============================================================
-- 039_email_delivery.sql -- Step 7.3 Email Delivery via Resend
--
-- email_preferences   per-user delivery toggles, unsubscribe capability
--                     token, bounce flag. Owner-only RLS.
-- email_delivery_log  per-attempt audit log: Resend message id, success,
--                     errors, delivered/opened/clicked/bounced timestamps
--                     populated by the /api/webhooks/resend handler.
--
-- The spec named this 038_email_preferences.sql; 038 is already taken
-- (038_reports_columns), so this is 039 -- the next free slot.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.email_preferences (
  user_id                       uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  tenant_id                     uuid,
  premarket_enabled             boolean DEFAULT true,
  midday_enabled                boolean DEFAULT true,
  close_enabled                 boolean DEFAULT true,
  urgent_alerts_only            boolean DEFAULT false,
  delivery_time_offset_minutes  int     DEFAULT 0,
  -- One-click unsubscribe capability URL token. Generated on first row
  -- creation and rotated on demand from the settings page.
  unsubscribe_token             text    DEFAULT gen_random_uuid()::text UNIQUE,
  -- Set true on a hard bounce; the cron route then skips this user.
  bounced                       boolean DEFAULT false,
  bounced_at                    timestamptz,
  bounce_reason                 text,
  created_at                    timestamptz DEFAULT now(),
  updated_at                    timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_prefs_tenant
  ON public.email_preferences (tenant_id);

ALTER TABLE public.email_preferences ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "owner_only" ON public.email_preferences;
CREATE POLICY "owner_only" ON public.email_preferences
  FOR ALL TO authenticated
  USING      (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

COMMENT ON COLUMN public.email_preferences.unsubscribe_token IS
  'One-click capability token embedded in unsubscribe links. Anyone with '
  'the URL can update this user''s preferences -- rotate via settings.';
COMMENT ON COLUMN public.email_preferences.delivery_time_offset_minutes IS
  'Per-user offset from the default brief schedule, for advisors in '
  'different time zones. The cron route compares (now - offset) against '
  'each brief''s nominal send time.';


CREATE TABLE IF NOT EXISTS public.email_delivery_log (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  report_id         uuid REFERENCES public.reports(id) ON DELETE CASCADE,
  user_id           uuid REFERENCES auth.users(id) ON DELETE CASCADE,
  tenant_id         uuid,

  attempted_at      timestamptz DEFAULT now(),
  attempt_number    int         DEFAULT 1,
  success           boolean     DEFAULT false,
  resend_message_id text,
  error_message     text,
  bytes_sent        int,
  truncated         boolean     DEFAULT false,
  subject_line      text,

  delivered_at      timestamptz,
  opened_at         timestamptz,
  clicked_at        timestamptz,
  bounced_at        timestamptz,
  bounce_type       text         -- 'hard' | 'soft' (populated by webhook)
);

CREATE INDEX IF NOT EXISTS idx_edl_report ON public.email_delivery_log (report_id);
CREATE INDEX IF NOT EXISTS idx_edl_user   ON public.email_delivery_log (user_id, attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_edl_resend ON public.email_delivery_log (resend_message_id);

ALTER TABLE public.email_delivery_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "owner_read" ON public.email_delivery_log;
CREATE POLICY "owner_read" ON public.email_delivery_log
  FOR SELECT TO authenticated USING (user_id = auth.uid());

COMMENT ON TABLE public.email_delivery_log IS
  'Per-attempt audit trail for report email delivery. One row per send '
  'attempt; Resend webhooks populate delivered_at / opened_at / clicked_at '
  '/ bounced_at after the fact via /api/webhooks/resend.';
