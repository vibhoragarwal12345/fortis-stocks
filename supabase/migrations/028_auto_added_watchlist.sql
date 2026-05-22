-- ============================================================
-- 028_auto_added_watchlist.sql
-- Auto-added watchlist tickers written by
-- pipeline/agents/peer_industry_analysis.py.
--
-- When the peer comparison finds a peer scoring >15 points higher than the
-- A-pick the system itself promoted, the BETTER peer is added here so the
-- next pre-market run can pull it into the candidate universe. This is the
-- "we tell the advisor when their pick isn't the strongest" discipline
-- written into the spec.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.auto_added_watchlist (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker             text NOT NULL,
  source_ticker      text NOT NULL,                 -- the A-pick that surfaced this
  source_run_date    date NOT NULL,
  source_run_type    text NOT NULL DEFAULT 'midday',
  peer_score_delta   numeric,                        -- how much better than the A-pick
  reason             text,
  consumed           boolean DEFAULT false,          -- next pre-market run sets this true
  consumed_at        timestamptz,
  created_at         timestamptz DEFAULT now(),
  UNIQUE (ticker, source_ticker, source_run_date, source_run_type)
);

CREATE INDEX IF NOT EXISTS idx_auto_added_watchlist_consumed
  ON public.auto_added_watchlist (consumed, created_at DESC);

ALTER TABLE public.auto_added_watchlist ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.auto_added_watchlist
  FOR SELECT TO authenticated USING (true);
