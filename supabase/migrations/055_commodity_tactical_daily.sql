-- 055_commodity_tactical_daily.sql
-- =====================================================================
-- Split the commodity scan into two cadences (owner directive, June 2026):
--   tactical  DAILY pre-market (Mon-Fri ~12:05 UTC, just after the stock
--             pre-market slot) -- short-term read (price, 1-week band, news);
--             the structural read is carried forward from the last full run.
--   full      WEEKLY Monday 22:45 UTC -- regenerates everything incl. the
--             long-horizon structural read.
-- Tactical signals move daily; structural fundamentals (EIA/COT weekly,
-- IMF/FRED monthly) do not. The doubled LLM budget covers the daily pass.
--
-- fire_github_dispatch gains an optional client_payload so the scan workflow
-- can read github.event.client_payload.mode.
-- =====================================================================

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Old 1-arg version is replaced by a (text, jsonb default '{}') overload. The
-- multibagger job's existing 1-arg call still resolves to it (default payload).
drop function if exists public.fire_github_dispatch(text);

create or replace function public.fire_github_dispatch(
  p_event_type text,
  p_payload jsonb default '{}'::jsonb
)
returns void
language plpgsql
security definer
set search_path = public, vault, net, extensions
as $$
declare
  v_pat  text;
  v_body jsonb;
begin
  select decrypted_secret into v_pat
    from vault.decrypted_secrets
   where name = 'github_pat_market_scan';

  if v_pat is null or length(v_pat) = 0 then
    raise warning 'fire_github_dispatch: Vault secret github_pat_market_scan not set; skipping %', p_event_type;
    return;
  end if;

  v_body := jsonb_build_object('event_type', p_event_type);
  if p_payload is not null and p_payload <> '{}'::jsonb then
    v_body := v_body || jsonb_build_object('client_payload', p_payload);
  end if;

  perform net.http_post(
    url     := 'https://api.github.com/repos/vibhoragarwal12345/fortis-stocks/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || v_pat,
      'Accept',        'application/vnd.github+json',
      'Content-Type',  'application/json',
      'User-Agent',    'fortis-supabase-pg-cron'
    ),
    body    := v_body
  );
end;
$$;

revoke all on function public.fire_github_dispatch(text, jsonb) from public;
revoke all on function public.fire_github_dispatch(text, jsonb) from anon, authenticated;

-- ---------------------------------------------------------------------
-- Commodity schedules (UTC). cron.schedule upserts by name (idempotent).
--   DAILY tactical : 12:05 UTC Mon-Fri  (right after the 12:00 stock pre-market)
--   WEEKLY full    : 22:45 UTC Monday
-- ---------------------------------------------------------------------
select cron.schedule(
  'commodity-scan-tactical-daily',
  '5 12 * * 1-5',
  $$select public.fire_github_dispatch('run-commodity-scan', '{"mode":"tactical"}'::jsonb)$$
);
select cron.schedule(
  'commodity-scan-weekly',
  '45 22 * * 1',
  $$select public.fire_github_dispatch('run-commodity-scan', '{"mode":"full"}'::jsonb)$$
);
