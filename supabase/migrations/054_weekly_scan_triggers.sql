-- 054_weekly_scan_triggers.sql
-- =====================================================================
-- Extend the punctual pg_cron -> GitHub repository_dispatch pattern
-- (migration 053, market scan) to the WEEKLY commodity + emerging-discovery
-- scans, so the ENTIRE pipeline fires itself on schedule from infrastructure
-- we control, instead of GitHub's native `schedule:` cron (which lags 15-60
-- min or silently drops a tick -- the original "scan didn't run" report).
--
-- After this, every scheduled scan is driven by pg_cron (one reliable source,
-- no duplicate runs). The native `schedule:` triggers are removed from the
-- commodity + multibagger-discovery workflows in the same change; their
-- repository_dispatch + workflow_dispatch triggers remain for on-demand runs.
--
-- The market scan already runs on pg_cron (jobs 1 + 2 from migration 053).
-- =====================================================================

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Generic dispatcher: POST a repository_dispatch with an arbitrary event type.
-- (fire_market_scan stays as-is; it carries a scan_type client_payload.)
create or replace function public.fire_github_dispatch(p_event_type text)
returns void
language plpgsql
security definer
set search_path = public, vault, net, extensions
as $$
declare
  v_pat text;
begin
  select decrypted_secret into v_pat
    from vault.decrypted_secrets
   where name = 'github_pat_market_scan';

  if v_pat is null or length(v_pat) = 0 then
    raise warning 'fire_github_dispatch: Vault secret github_pat_market_scan is not set; skipping %', p_event_type;
    return;
  end if;

  perform net.http_post(
    url     := 'https://api.github.com/repos/vibhoragarwal12345/fortis-stocks/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || v_pat,
      'Accept',        'application/vnd.github+json',
      'Content-Type',  'application/json',
      'User-Agent',    'fortis-supabase-pg-cron'
    ),
    body    := jsonb_build_object('event_type', p_event_type)
  );
end;
$$;

revoke all on function public.fire_github_dispatch(text) from public;
revoke all on function public.fire_github_dispatch(text) from anon, authenticated;

-- ---------------------------------------------------------------------
-- Weekly Monday schedules (UTC; Supabase pg_cron runs in UTC).
--   15:00 UTC Mon  emerging multibagger discovery  -> run-multibagger-discovery
--   22:45 UTC Mon  commodity scan (1-week bands)    -> run-commodity-scan
-- cron.schedule(name, ...) upserts by name, so re-running this is idempotent.
-- ---------------------------------------------------------------------
select cron.schedule(
  'multibagger-discovery-weekly',
  '0 15 * * 1',
  $$select public.fire_github_dispatch('run-multibagger-discovery')$$
);
select cron.schedule(
  'commodity-scan-weekly',
  '45 22 * * 1',
  $$select public.fire_github_dispatch('run-commodity-scan')$$
);
