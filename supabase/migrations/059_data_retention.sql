-- 059_data_retention.sql
-- =====================================================================
-- Cap news_items growth so the database stays inside the Supabase free-tier
-- 500 MB ceiling.
--
-- Why this is needed (audit 2026-08-17): the database had reached 1080 MB,
-- of which news_items alone was 727 MB (67%) -- 620k rows covering only
-- May-Aug 2026, growing ~145k rows / ~170 MB per month. The UNIQUE(url)
-- index accounted for 310 MB of that on its own, because harvested URLs
-- average 300 characters.
--
-- Why 14 days is safe. Every consumer of news_items reads a short window:
--   catalyst_agent        NEWS_WINDOW_HOURS = 24h
--   debate_synthesizer    since1d           = 36h
--   crowd_intelligence    NEWS_WINDOW_HOURS = 48h
--   sentiment_scorer      BASELINE_DAYS     = 7 days   <-- longest
--   news_resolver         full scan (maintenance; benefits from a smaller table)
-- The web app never queries news_items at all. 14 days leaves 2x headroom
-- over the longest window.
--
-- Why purging cannot break de-duplication. news_harvester upserts with
-- on_conflict="url" but discards any article older than MAX_AGE_HOURS = 24
-- before it ever reaches the insert. Rows deleted at 14 days are therefore
-- far outside the range the harvester would re-offer, so removing them cannot
-- cause duplicate re-inserts.
--
-- Historical sentiment is NOT lost: ticker_sentiment_rollup stores the
-- derived per-ticker aggregates and is untouched by this job.
--
-- cron.schedule upserts by name -> idempotent.
-- =====================================================================

create extension if not exists pg_cron;

-- ── Retention function ────────────────────────────────────────────────
-- Kept as a function (rather than inline SQL in the cron command) so the
-- window lives in one place and the job can be run by hand for testing:
--   select public.purge_old_news();

create or replace function public.purge_old_news(retain_days integer default 14)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  deleted integer;
begin
  delete from public.news_items
   where published_at < now() - make_interval(days => retain_days);

  get diagnostics deleted = row_count;

  raise log 'purge_old_news: deleted % rows older than % days', deleted, retain_days;
  return deleted;
end;
$$;

comment on function public.purge_old_news(integer) is
  'Deletes news_items older than retain_days (default 14). The longest consumer '
  'window is sentiment_scorer.BASELINE_DAYS = 7, so 14 days keeps 2x headroom. '
  'Scheduled nightly as the news-retention-daily pg_cron job (migration 059).';

-- ── Nightly schedule ──────────────────────────────────────────────────
-- 03:20 UTC: after the 22:45 Monday commodity run and well before the
-- 10:30 UTC data harvest, so the purge never overlaps a writer.

select cron.schedule(
  'news-retention-daily',
  '20 3 * * *',
  $$select public.purge_old_news(14)$$
);
