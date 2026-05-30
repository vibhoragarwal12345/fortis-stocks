-- ============================================================
-- 045_market_scans.sql -- Lean rewrite PART 3
--
-- The two-speed funnel's persistence layer:
--
--   market_scans   One row per scan run. Stamps when it started, what type,
--                  how many tickers were scanned, what shortlist size made
--                  it past Layer 2, and (final) when the deep-analysis +
--                  grading completed. The dashboard reads the LATEST row
--                  with status='complete' to know which data to show.
--
--   scan_results   Per-ticker Layer-1 metrics for every name in the
--                  universe at scan time. Carries the cheap signals that
--                  drove the Layer-2 composite_score. The top N by score
--                  get advanced=true and flow into ranked_focus_list for
--                  Layer 3 deep analysis.
--
-- ranked_focus_list keeps its existing schema; we add scan_id so each
-- graded pick lineage-links back to the originating scan.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.market_scans (
  id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id             uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid,
  scan_timestamp        timestamptz NOT NULL DEFAULT now(),
  scan_type             text NOT NULL CHECK (scan_type IN ('premarket','intraday','postclose','manual')),
  status                text NOT NULL DEFAULT 'running'
                         CHECK (status IN ('running','complete','failed')),

  tickers_scanned_count int,
  shortlist_count       int,
  graded_count          int,

  started_at            timestamptz NOT NULL DEFAULT now(),
  layer1_completed_at   timestamptz,
  layer2_completed_at   timestamptz,
  layer3_completed_at   timestamptz,
  layer4_completed_at   timestamptz,
  completed_at          timestamptz,

  notes                 text
);

CREATE INDEX IF NOT EXISTS idx_market_scans_tenant_status
  ON public.market_scans (tenant_id, status, scan_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_market_scans_completed
  ON public.market_scans (status, completed_at DESC)
  WHERE status = 'complete';


CREATE TABLE IF NOT EXISTS public.scan_results (
  id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id             uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid,
  scan_id               bigint NOT NULL REFERENCES public.market_scans(id) ON DELETE CASCADE,
  ticker                text NOT NULL,

  -- ── Layer 1 raw metrics (all cheap, pure-math) ────────────────────
  price                 numeric,
  day_change_pct        numeric,
  gap_pct               numeric,
  return_5d_pct         numeric,
  return_20d_pct        numeric,
  relative_volume       numeric,         -- today vs 20d avg
  rsi_14                numeric,
  pct_from_52w_high     numeric,
  pct_from_52w_low      numeric,
  is_breakout           boolean,
  is_breakdown          boolean,

  -- ── Layer 2 composite ─────────────────────────────────────────────
  composite_score       numeric,
  rank                  int,
  advanced              boolean NOT NULL DEFAULT false,

  -- snapshot data for debug + audit
  data_as_of            timestamptz,
  notes                 text,
  created_at            timestamptz NOT NULL DEFAULT now(),

  UNIQUE (scan_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_scan_results_scan_score
  ON public.scan_results (scan_id, composite_score DESC);

CREATE INDEX IF NOT EXISTS idx_scan_results_ticker
  ON public.scan_results (ticker, scan_id DESC);


-- ranked_focus_list gets a scan lineage column so each graded pick points
-- back to its originating scan. Nullable for backfill compatibility.
ALTER TABLE public.ranked_focus_list
  ADD COLUMN IF NOT EXISTS scan_id bigint REFERENCES public.market_scans(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_ranked_focus_list_scan
  ON public.ranked_focus_list (scan_id);


-- ── RLS (tenant_isolation pattern) ──────────────────────────────────
ALTER TABLE public.market_scans  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scan_results  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON public.market_scans;
CREATE POLICY tenant_isolation ON public.market_scans
  FOR ALL TO authenticated
  USING      (public.is_tenant_member(tenant_id))
  WITH CHECK (public.is_tenant_member(tenant_id));

DROP POLICY IF EXISTS tenant_isolation ON public.scan_results;
CREATE POLICY tenant_isolation ON public.scan_results
  FOR ALL TO authenticated
  USING      (public.is_tenant_member(tenant_id))
  WITH CHECK (public.is_tenant_member(tenant_id));
