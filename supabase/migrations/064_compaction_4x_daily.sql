-- 064_compaction_4x_daily.sql
-- =====================================================================
-- Compact 4x daily instead of once, so the database stops breaching the
-- free-tier quota between compactions.
--
-- WHY. Migration 060 added a single nightly VACUUM FULL at 03:30. That was not
-- enough: the database has a ~50 MB DAILY SWING from update churn, so it
-- climbed past the ceiling every afternoon and was only rescued the next
-- morning. Measured 2026-08-21/22:
--     03:30 compaction  -> 458 MB
--     17:39 .. 02:02    -> 508 MB   (8 MB OVER the 500 MB quota)
--     03:30 compaction  -> 458 MB
-- The anomaly monitor flagged it CRITICAL for eight hours straight and opened
-- issue #28. Being over quota risks Supabase putting the project read-only,
-- which is a platform outage.
--
-- Compacting every ~6h caps the accumulated swing at roughly a quarter of that,
-- keeping the peak near 470 MB with ~30 MB of margin -- without deleting
-- anything.
--
-- TIMING. VACUUM FULL takes an ACCESS EXCLUSIVE lock, so every slot sits in a
-- gap where nothing else is scheduled:
--     03:30  after the 03:20 retention purge (unchanged, migration 060)
--     08:00  before the 09:00 fundamentals refresh and 10:30 harvest
--     14:30  after the 12:00 scan finishes (~13:05), before the 16:30 scan
--     20:00  after the 16:30 scan finishes, before the 22:45 Monday commodity
--
-- SCOPE. news_items (the churn source) plus scan_results, which is the largest
-- table and carries the most dead tuples after a scan writes 3,347 rows twice a
-- day. Both are quick: the whole job runs in seconds.
--
-- cron.schedule upserts by name -> idempotent.
-- =====================================================================

create extension if not exists pg_cron;

select cron.schedule('news-compaction-daily',  '30 3 * * *',
  'vacuum (full, analyze) public.news_items');

select cron.schedule('compaction-morning',     '0 8 * * *',
  'vacuum (full, analyze) public.news_items');

select cron.schedule('compaction-midday',      '30 14 * * *',
  'vacuum (full, analyze) public.scan_results');

select cron.schedule('compaction-evening',     '0 20 * * *',
  'vacuum (full, analyze) public.news_items');
