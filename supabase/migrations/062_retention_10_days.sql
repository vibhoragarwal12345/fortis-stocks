-- 062_retention_10_days.sql
-- =====================================================================
-- Tighten news_items retention from 14 days to 10.
--
-- WHY. On 2026-08-21 the database sat at 474 MB of the 500 MB free-tier quota
-- with only 26 MB of headroom, and a VACUUM FULL reclaimed just 4 MB -- i.e.
-- it is real data now, not bloat, so compaction (migration 060) alone cannot
-- create room. news_items is 129 MB / 125,670 rows and is the only large table
-- that can be trimmed without touching analytical history: scan_results is the
-- backtest baseline and is deliberately left alone.
--
-- WHY 10 IS STILL SAFE. The longest consumer window is unchanged --
-- sentiment_scorer.BASELINE_DAYS = 7 days -- so 10 keeps three full days of
-- margin, and the web app never reads this table at all. Purging cannot break
-- URL de-duplication either, because news_harvester discards anything older
-- than MAX_AGE_HOURS = 24 before the upsert.
--
-- Removes ~25,264 rows (~26 MB), roughly doubling headroom.
--
-- cron.schedule upserts by name -> idempotent.
-- =====================================================================

select cron.schedule(
  'news-retention-daily',
  '20 3 * * *',
  $$select public.purge_old_news(10)$$
);
