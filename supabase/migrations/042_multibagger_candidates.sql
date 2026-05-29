-- ============================================================
-- 042_multibagger_candidates.sql -- Multibagger Discovery Engine
--
-- multibagger_candidates  Latest screener+scorer output. Refreshed monthly.
-- emerging_watchlist      Curated, persistent watchlist of candidates that
--                         have advanced past deep research. Tracks
--                         conviction tier, thesis, key metrics, and the
--                         status of the thesis over time.
-- multibagger_theses      Generated deep-research theses (one row per
--                         candidate per generation). Append-only; the
--                         watchlist references the latest by ticker.
--
-- Spec named this 041; 041 was multi-tenancy, so this is 042.
-- All three tables are tenant-scoped and default to the Fortis tenant.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.multibagger_candidates (
  id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id                uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid,
  ticker                   text NOT NULL,
  screen_date              date NOT NULL,
  name                     text,
  sector                   text,
  industry                 text,
  market_cap               numeric,

  -- Stage 1 -- the 6 DNA traits
  trait_scores             jsonb NOT NULL DEFAULT '{}'::jsonb,
  traits_passed            int   NOT NULL DEFAULT 0,

  -- Key raw metrics behind the traits
  revenue_cagr_3y          numeric,
  revenue_growth_ttm_yoy   numeric,
  gross_margin_ttm         numeric,
  gross_margin_trend       numeric,
  operating_margin_ttm     numeric,
  operating_margin_trend   numeric,
  insider_ownership_pct    numeric,
  analyst_count            int,
  institutional_ownership_pct numeric,
  debt_to_equity           numeric,
  cash_runway_months       numeric,

  -- Stage 2 -- score
  multibagger_score        numeric,
  growth_quality_score     numeric,
  reinvestment_score       numeric,
  unit_economics_score     numeric,
  alignment_score          numeric,
  discovery_score          numeric,
  score_caps_applied       jsonb NOT NULL DEFAULT '[]'::jsonb,

  -- Pipeline status
  advanced_to_research     boolean NOT NULL DEFAULT false,
  notes                    text,
  created_at               timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, ticker, screen_date)
);

CREATE INDEX IF NOT EXISTS idx_mbcand_tenant_date ON public.multibagger_candidates (tenant_id, screen_date DESC);
CREATE INDEX IF NOT EXISTS idx_mbcand_score      ON public.multibagger_candidates (multibagger_score DESC);


CREATE TABLE IF NOT EXISTS public.multibagger_theses (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id           uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid,
  ticker              text NOT NULL,
  generated_at        timestamptz NOT NULL DEFAULT now(),
  multibagger_score   numeric,
  conviction_tier     text CHECK (conviction_tier IN ('tier_1_high_conviction','tier_2_promising','tier_3_speculative')),
  risk_rating         text CHECK (risk_rating IN ('speculative','high_risk','moderate_risk')),

  business_model       text,
  the_10x_path         text,
  moat_assessment      text,
  founder_assessment   text,
  what_has_to_go_right text,
  what_kills_it        text,
  key_metrics_to_track jsonb NOT NULL DEFAULT '[]'::jsonb,

  -- factcheck output
  verification_score   numeric,
  quality_grade        text,
  factcheck_details    jsonb,

  data_snapshot        jsonb,
  llm_model            text,
  notes                text
);

CREATE INDEX IF NOT EXISTS idx_mbthesis_ticker ON public.multibagger_theses (tenant_id, ticker, generated_at DESC);


CREATE TABLE IF NOT EXISTS public.emerging_watchlist (
  id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id            uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid,
  ticker               text NOT NULL,
  added_date           date NOT NULL DEFAULT CURRENT_DATE,
  conviction_tier      text CHECK (conviction_tier IN ('tier_1_high_conviction','tier_2_promising','tier_3_speculative')),

  entry_price          numeric,
  entry_market_cap     numeric,
  thesis_summary       text,
  the_10x_path         text,
  what_kills_it        text,
  key_metrics_to_track jsonb NOT NULL DEFAULT '[]'::jsonb,
  multibagger_score    numeric,

  status               text NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','thesis_intact','thesis_weakening','thesis_broken','graduated','archived')),
  last_reviewed_date   date,
  current_price        numeric,
  current_market_cap   numeric,
  return_since_added   numeric,
  peak_return_pct      numeric,
  max_drawdown_pct     numeric,
  notes                text,
  thesis_id            bigint REFERENCES public.multibagger_theses(id) ON DELETE SET NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_ewl_tenant_status ON public.emerging_watchlist (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_ewl_tier          ON public.emerging_watchlist (conviction_tier);


-- ── RLS (tenant_isolation pattern from migration 041) ───────────────
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['multibagger_candidates','multibagger_theses','emerging_watchlist']
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', t);
    EXECUTE format($f$
      CREATE POLICY tenant_isolation ON public.%I
      FOR ALL TO authenticated
      USING      (public.is_tenant_member(tenant_id))
      WITH CHECK (public.is_tenant_member(tenant_id))
    $f$, t);
  END LOOP;
END $$;
