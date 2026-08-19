-- 060_nightly_compaction.sql
-- =====================================================================
-- Nightly compaction of news_items, so bloat stops silently eating the
-- Supabase free-tier 500 MB budget.
--
-- WHY. Retention (migration 059) bounds the ROW COUNT, but not the space.
-- news_items is the churniest table on the platform: news_resolver writes
-- resolution_status to every new row and sentiment_scorer then writes
-- sentiment_score to it, so each row is rewritten ~2x after insert, and every
-- UPDATE leaves a dead tuple plus index garbage. Plain autovacuum makes that
-- space reusable but never returns it to the OS, and index bloat keeps growing.
--
-- Measured on 2026-08-19: the database drifted to 525 MB (25 MB OVER quota)
-- from a single day of that churn -- 141,928 live rows and ZERO dead tuples,
-- yet the table read 200 MB. VACUUM FULL took the database back to 467 MB,
-- reclaiming 58 MB. That was the second manual rescue in one day, which makes
-- it a missing mechanism rather than an incident.
--
-- TIMING. 03:30 UTC: after the 03:20 retention purge (059), which deletes the
-- rows this then compacts away, and ~7 hours before the 10:30 UTC data
-- harvest. VACUUM FULL takes an ACCESS EXCLUSIVE lock and rewrites the table,
-- so it must never overlap a writer -- at 03:30 nothing else is scheduled.
--
-- WHY NOT A FUNCTION. VACUUM cannot run inside a transaction block, and every
-- PL/pgSQL function body is one -- wrapping this in a function fails at
-- runtime with "VACUUM cannot be executed from a function" (verified against
-- this database before shipping). pg_cron executes its command string
-- directly, outside a transaction, so the raw statement is scheduled instead.
--
-- SCOPE. news_items only. It is the churn source; the other large tables
-- (scan_results, multibagger_candidates) are append-mostly and do not need a
-- nightly rewrite. One table also keeps the exclusive-lock window short.
-- =====================================================================

create extension if not exists pg_cron;

select cron.schedule(
  'news-compaction-daily',
  '30 3 * * *',
  'vacuum (full, analyze) public.news_items'
);
