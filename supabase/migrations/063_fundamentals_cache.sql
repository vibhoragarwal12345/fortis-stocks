-- 063_fundamentals_cache.sql
-- Renumbered from the branch-local 057_fundamentals_cache.sql: main already
-- has a DIFFERENT 057 (057_scan_alpha_factors.sql), so two migrations shared
-- that number. The table itself was already applied to production by hand
-- (3,342 rows on 2026-08-21); this commits the DDL so the migrations
-- directory finally describes the live schema. IF NOT EXISTS makes it a
-- no-op against the existing table.

--
-- WHY A TABLE (not the on-disk _finnhub_cache): the scan runs on ephemeral
-- GitHub Actions runners whose local disk is WIPED between runs, so a per-run
-- disk cache is useless in production -- every scan would re-hammer the Finnhub
-- free tier and rate-limit itself to death. Fundamentals therefore live here,
-- warmed by a separate scheduled refresh job (refresh_fundamentals_cache.py,
-- ~470 tickers/day so a weekly cycle covers the ~3,300 universe inside free
-- limits) and read by the scan's factor ranker as a single fast batch SELECT.
--
-- fetched_at is the freshness stamp the refresher uses to decide staleness and
-- that a future point-in-time backtest can filter on. This is reference data,
-- not tenant-scoped: authenticated users may read; the pipeline writes via the
-- service_role key (bypasses RLS).

CREATE TABLE IF NOT EXISTS public.fundamentals (
  ticker          text PRIMARY KEY,
  sector          text,
  market_cap_usd  numeric,
  -- quality / profitability
  gross_margin    numeric,
  oper_margin     numeric,
  net_margin      numeric,
  roe             numeric,
  roa             numeric,
  debt_equity     numeric,
  current_ratio   numeric,
  rev_growth      numeric,
  -- value
  pe              numeric,
  pfcf_share      numeric,
  ps              numeric,
  -- risk
  beta            numeric,
  avg_vol_10d_m   numeric,     -- 10-day avg trading volume, millions of shares
  -- provenance
  source          text,        -- 'finnhub' | 'finnhub+yfinance'
  fetched_at      timestamptz DEFAULT now(),
  updated_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_fetched_at
  ON public.fundamentals (fetched_at);

ALTER TABLE public.fundamentals ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'fundamentals' AND policyname = 'authenticated_read'
  ) THEN
    CREATE POLICY "authenticated_read" ON public.fundamentals
      FOR SELECT TO authenticated USING (true);
  END IF;
END $$;
