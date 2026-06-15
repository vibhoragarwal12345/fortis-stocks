-- 053_market_scan_external_trigger.sql
-- =====================================================================
-- PRIMARY, PUNCTUAL trigger for the market scan.
--
-- GitHub Actions' native `schedule:` cron is unreliable for a market-time-
-- critical job -- under load it lags 15-60 min or silently drops a tick
-- (root cause of the "pre-market scan didn't run" report, June 15 2026).
--
-- This moves the trigger into infrastructure we already own and control:
-- Supabase pg_cron fires on time, and pg_net POSTs a `repository_dispatch`
-- to GitHub so the scan workflow starts immediately at 12:00 + 16:30 UTC
-- (Mon-Fri). The native GitHub crons stay as a backup.
--
-- The GitHub token is read from Supabase Vault (never stored in plaintext
-- here). Before this fires for real you must store a fine-grained PAT:
--
--   select vault.create_secret(
--     '<github_fine_grained_PAT>',          -- repo: Actions = Read+Write
--     'github_pat_market_scan',
--     'PAT for pg_cron -> market scan repository_dispatch');
--
-- (If the secret is absent the function no-ops with a warning -- safe.)
-- =====================================================================

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- ---------------------------------------------------------------------
-- Fire a repository_dispatch at GitHub to start the market scan.
-- ---------------------------------------------------------------------
create or replace function public.fire_market_scan(p_scan_type text)
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
    raise warning 'fire_market_scan: Vault secret github_pat_market_scan is not set; skipping dispatch';
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
    body    := jsonb_build_object(
      'event_type',     'run-market-scan',
      'client_payload', jsonb_build_object('scan_type', p_scan_type)
    )
  );
end;
$$;

-- The scan must only be triggerable by the scheduler (postgres), never by a
-- client role. Lock it down.
revoke all on function public.fire_market_scan(text) from public;
revoke all on function public.fire_market_scan(text) from anon, authenticated;

-- ---------------------------------------------------------------------
-- Schedule (UTC; Supabase pg_cron runs in UTC). Mon-Fri only.
--   12:00 UTC  pre-market   -> scan_type 'premarket'
--   16:30 UTC  midday       -> scan_type 'intraday'  (valid run_scan label)
-- cron.schedule(name, ...) upserts by name, so re-running this is idempotent.
-- ---------------------------------------------------------------------
select cron.schedule(
  'market-scan-premarket',
  '0 12 * * 1-5',
  $$select public.fire_market_scan('premarket')$$
);
select cron.schedule(
  'market-scan-midday',
  '30 16 * * 1-5',
  $$select public.fire_market_scan('intraday')$$
);
