-- ============================================================
-- 049_commodity_scans.sql -- Commodities pipeline Part C
--
--   commodity_scans   One row per commodity scan run. `payload` holds the
--                     full analyze.run() output (per-commodity layers +
--                     dual-horizon narratives, every figure verification-
--                     labeled). `cross_link` holds the three deterministic
--                     macro signals (copper -> growth, gold -> risk,
--                     oil -> inflation) the stock engine consumes.
--                     The dashboard reads the LATEST row only.
--
--   macro_context.commodity_signals   The stock macro agent stamps the
--                     cross-link snapshot it consumed into its own row,
--                     so every regime classification is auditable back
--                     to the commodity scan that informed it.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.commodity_scans (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id        uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid,
  scan_time        timestamptz NOT NULL DEFAULT now(),
  status           text NOT NULL DEFAULT 'complete'
                     CHECK (status IN ('complete','partial','failed')),

  commodity_count  int,
  narratives_ok    int,   -- published narratives (max 2 per commodity)
  narratives_total int,

  payload          jsonb NOT NULL,
  cross_link       jsonb,

  notes            text
);

CREATE INDEX IF NOT EXISTS idx_commodity_scans_latest
  ON public.commodity_scans (status, scan_time DESC);

ALTER TABLE public.commodity_scans ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON public.commodity_scans;
CREATE POLICY tenant_isolation ON public.commodity_scans
  FOR ALL TO authenticated
  USING      (public.is_tenant_member(tenant_id))
  WITH CHECK (public.is_tenant_member(tenant_id));

-- The stock engine's macro snapshot records which commodity signals it saw.
ALTER TABLE public.macro_context
  ADD COLUMN IF NOT EXISTS commodity_signals jsonb;
