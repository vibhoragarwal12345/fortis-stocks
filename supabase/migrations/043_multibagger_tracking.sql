-- ============================================================
-- 043_multibagger_tracking.sql -- track record for the engine
--
-- multibagger_outcomes  One row per name ever added to the watchlist.
--                       Holds the long-tail return data: peak, drawdown,
--                       2x/5x/10x milestone dates, post-mortem text for
--                       broken theses. The tracker reports the honest
--                       hit-rate from this table -- including failures.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.multibagger_outcomes (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id           uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'::uuid,
  ticker              text NOT NULL,
  added_date          date NOT NULL,
  conviction_tier     text,
  entry_price         numeric,
  entry_market_cap    numeric,

  current_price       numeric,
  current_return_pct  numeric,
  peak_return_pct     numeric,
  peak_date           date,
  max_drawdown_pct    numeric,

  milestones          jsonb NOT NULL DEFAULT '{}'::jsonb,
  final_status        text,
  holding_period_days int,
  post_mortem         text,

  last_updated        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, ticker, added_date)
);

CREATE INDEX IF NOT EXISTS idx_mbout_tenant ON public.multibagger_outcomes (tenant_id, last_updated DESC);

ALTER TABLE public.multibagger_outcomes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.multibagger_outcomes;
CREATE POLICY tenant_isolation ON public.multibagger_outcomes
  FOR ALL TO authenticated
  USING      (public.is_tenant_member(tenant_id))
  WITH CHECK (public.is_tenant_member(tenant_id));
