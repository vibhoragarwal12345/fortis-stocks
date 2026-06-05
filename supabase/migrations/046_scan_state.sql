-- ============================================================
-- 046_scan_state.sql -- shared cached-scan state + smart manual refresh
--
-- A single global row that is the source of truth for "what is the latest
-- scan and is one running right now". The market scan is the WHOLE liquid US
-- market, identical for every user/tenant, so this state is global (no
-- tenant_id) -- one cached scan serves everyone.
--
-- Written only by the pipeline (run_scan, via service role) and the
-- /api/scan/trigger endpoint (also service role). Authenticated users read it
-- (directly or via /api/scan/status) to drive the dashboard refresh control.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.scan_state (
  id                       int PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton
  latest_scan_id           bigint REFERENCES public.market_scans(id) ON DELETE SET NULL,
  latest_scan_completed_at timestamptz,
  current_status           text NOT NULL DEFAULT 'idle'
                            CHECK (current_status IN ('idle','running','failed')),
  running_since            timestamptz,
  triggered_by             text CHECK (triggered_by IN ('cron','manual')),
  last_error               text,
  updated_at               timestamptz NOT NULL DEFAULT now()
);

-- Seed the singleton from the most recent completed scan (if any exists).
INSERT INTO public.scan_state
  (id, latest_scan_id, latest_scan_completed_at, current_status)
SELECT 1, ms.id, ms.completed_at, 'idle'
FROM public.market_scans ms
WHERE ms.status = 'complete'
ORDER BY ms.completed_at DESC NULLS LAST
LIMIT 1
ON CONFLICT (id) DO NOTHING;

-- Guarantee the row exists even with zero completed scans.
INSERT INTO public.scan_state (id, current_status)
VALUES (1, 'idle')
ON CONFLICT (id) DO NOTHING;

-- ── RLS: authenticated may read; writes go through the service role only ──
ALTER TABLE public.scan_state ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS scan_state_read ON public.scan_state;
CREATE POLICY scan_state_read ON public.scan_state
  FOR SELECT TO authenticated
  USING (true);
