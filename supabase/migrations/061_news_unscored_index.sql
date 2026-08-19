-- 061_news_unscored_index.sql
-- =====================================================================
-- Partial index for "unscored but verified news, in id order" -- the query
-- sentiment_scorer._score_news pages through on every harvest.
--
-- Migration 059/keyset paging fixed the O(n^2) OFFSET problem, but exposed a
-- second, different one: the filter itself was unindexed. The 2026-08-19
-- harvest still died on 57014 with
--   GET /news_items?select=...&sentiment_score=is.null
--                  &resolution_status=eq.verified&order=id
-- because unscored rows are the NEWEST rows (highest ids), so Postgres walked
-- the primary key from the start and discarded 128,393 rows to fill one
-- 1,000-row page: 4,992 ms per page, ~7 pages, well past the 2-minute
-- statement_timeout. The rollup was never rebuilt that day.
--
-- Measured on this database:
--   before   Index Scan using news_items_pkey, Rows Removed by Filter 128393
--            Execution Time 4992.524 ms
--   after    Index Scan using idx_news_items_unscored, no filter step
--            Execution Time    2.468 ms      (~2000x, index is 160 kB)
--
-- Partial on purpose: it only ever contains rows still waiting to be scored
-- (~6k), not the whole table, so it stays tiny and costs almost nothing to
-- maintain -- which matters on a 500 MB budget.
-- =====================================================================

create index if not exists idx_news_items_unscored
  on public.news_items (id)
  where sentiment_score is null and resolution_status = 'verified';

-- ── db_size_mb(): let the anomaly monitor watch the free-tier budget ──────
-- PostgREST cannot run arbitrary SQL, so the monitor needs an RPC to read
-- database size. On 2026-08-19 the database silently reached 525 MB -- 25 MB
-- OVER the 500 MB quota -- purely from a day of update churn. Crossing the
-- ceiling can put the project into read-only, i.e. a platform outage, so this
-- is alarmed with room to act rather than at the cliff.

create or replace function public.db_size_mb()
returns numeric
language sql
security definer
set search_path = public
as $$
  select round(pg_database_size(current_database()) / 1024.0 / 1024.0, 1);
$$;

comment on function public.db_size_mb() is
  'Current database size in MB. Read by pipeline/anomaly_monitor.py to alert '
  'before the Supabase free-tier 500 MB quota is breached.';

grant execute on function public.db_size_mb() to service_role;
