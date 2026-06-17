-- 056_data_harvest_schedule.sql
-- =====================================================================
-- Schedule the pre-scan data harvest (Tier A: news / SEC / options) via the
-- same reliable pg_cron -> repository_dispatch path as the scans. Runs 10:30
-- UTC Mon-Fri, ~90 min before the 12:00 UTC pre-market stock scan, so
-- debate_synthesizer + catalyst_agent read fresh news_items / sec_filings /
-- options_signals instead of the stale rows the audit found (June 2026).
--
-- The harvest workflow is independent of run_scan, so this never affects the
-- scan's reliability. fire_github_dispatch already exists (migration 055).
-- cron.schedule upserts by name -> idempotent.
-- =====================================================================

create extension if not exists pg_cron;
create extension if not exists pg_net;

select cron.schedule(
  'data-harvest-daily',
  '30 10 * * 1-5',
  $$select public.fire_github_dispatch('run-data-harvest')$$
);
