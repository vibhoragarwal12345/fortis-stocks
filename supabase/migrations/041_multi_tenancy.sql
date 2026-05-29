-- ============================================================
-- 041_multi_tenancy.sql -- Step 7.6 MVP multi-tenancy
--
-- Adds the four governance tables (tenants, platform_admins,
-- tenant_members, tenant_invites), the helper functions used by every
-- RLS policy, a fixed Fortis tenant, a backfill of every existing
-- tenant-scoped row into Fortis, and a wholesale RLS rewrite that
-- replaces the old owner-only policies with tenant-scoped ones.
--
-- Deliberately EXCLUDED: billing, subscriptions, Stripe, pricing,
-- trial expirations, payment gating. Access is controlled by
-- tenants.access_status + tenants.feature_flags only.
--
-- Spec called this 040_multi_tenancy.sql; 040 was taken by
-- feedback_collection (step 7.5), so this is 041.
-- ============================================================


-- ── 0. Constants ────────────────────────────────────────────────────
-- The Fortis Agency UUID is fixed so that older code paths (the report
-- composer, the cron route, the daily aggregator) can keep inserting
-- with a stable DEFAULT until they learn about tenants in a later
-- commit. New tenants get a normal gen_random_uuid().
--
--   The Fortis Agency  ->  00000000-0000-4000-8000-000000000001


-- ── 1. tenants ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.tenants (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                  text NOT NULL,
  slug                  text NOT NULL UNIQUE,
  logo_url              text,
  primary_color         text NOT NULL DEFAULT '#0F6E56',
  email_from_name       text,
  access_status         text NOT NULL DEFAULT 'active'
                          CHECK (access_status IN ('active','suspended','archived')),
  access_granted_until  timestamptz,
  feature_flags         jsonb NOT NULL DEFAULT '{
    "premarket_report":     true,
    "midday_report":        true,
    "close_report":         true,
    "multiple_portfolios":  true,
    "white_label":          true,
    "custom_email":         true,
    "max_portfolios":       50,
    "max_holdings":         500
  }'::jsonb,
  notes                 text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  created_by_user_id    uuid REFERENCES auth.users(id) ON DELETE SET NULL
);

COMMENT ON COLUMN public.tenants.access_status IS
  'active | suspended | archived. Replaces any subscription_status concept '
  '-- billing is deliberately out of scope for the MVP.';
COMMENT ON COLUMN public.tenants.access_granted_until IS
  'NULL = indefinite access. Otherwise tenants whose timestamp is in the '
  'past get the same friendly "access ended" message as suspended ones.';
COMMENT ON COLUMN public.tenants.feature_flags IS
  'Per-tenant on/off + numeric flags. Resolved by canAccessFeature() in '
  'src/lib/permissions.ts. Adding a flag requires no migration -- just '
  'set it on the tenant row.';


-- ── 2. platform_admins ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.platform_admins (
  user_id    uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.platform_admins IS
  'Whoever is in this table can reach /admin and manage every tenant. '
  'Add yourself via the Supabase SQL editor (the migration auto-adds the '
  'configured admin email if that user exists at apply time).';


-- ── 3. tenant_members ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.tenant_members (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id   uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role        text NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member')),
  invited_by  uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  invited_at  timestamptz NOT NULL DEFAULT now(),
  accepted_at timestamptz,
  UNIQUE (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_members_user  ON public.tenant_members (user_id);
CREATE INDEX IF NOT EXISTS idx_tenant_members_tenant ON public.tenant_members (tenant_id);


-- ── 4. tenant_invites ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.tenant_invites (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id           uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
  email               text NOT NULL,
  role                text NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member')),
  -- Reasonably-collision-resistant URL token. gen_random_uuid()::text is
  -- 36 chars; doubling it gets us ~64 hex chars of entropy.
  token               text NOT NULL UNIQUE DEFAULT
                        translate(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''),
  invited_by          uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  invited_at          timestamptz NOT NULL DEFAULT now(),
  expires_at          timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
  accepted_at         timestamptz,
  accepted_by_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_tenant_invites_open
  ON public.tenant_invites (lower(email)) WHERE accepted_at IS NULL;


-- ── 5. Helper functions used by every RLS policy ────────────────────
-- SECURITY DEFINER bypasses RLS on the lookup tables so we can use these
-- inside the very policies that protect those tables -- no recursion.

CREATE OR REPLACE FUNCTION public.is_tenant_member(p_tenant uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.tenant_members
    WHERE user_id = auth.uid() AND tenant_id = p_tenant
  )
$$;

CREATE OR REPLACE FUNCTION public.is_tenant_admin(p_tenant uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.tenant_members
    WHERE user_id = auth.uid() AND tenant_id = p_tenant AND role = 'admin'
  )
$$;

CREATE OR REPLACE FUNCTION public.is_platform_admin()
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.platform_admins WHERE user_id = auth.uid()
  )
$$;

CREATE OR REPLACE FUNCTION public.current_tenant_ids()
RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT COALESCE(array_agg(tenant_id), ARRAY[]::uuid[])
  FROM public.tenant_members WHERE user_id = auth.uid()
$$;

-- Anonymous-friendly invite lookup. The signup page calls this with the
-- token from the URL to render "joining <tenant> as <email>". We expose
-- only what the signup needs -- never the underlying token table.
CREATE OR REPLACE FUNCTION public.lookup_invite(p_token text)
RETURNS TABLE (
  tenant_id   uuid,
  tenant_name text,
  email       text,
  role        text,
  expires_at  timestamptz,
  is_valid    boolean
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT
    ti.tenant_id,
    t.name,
    ti.email,
    ti.role,
    ti.expires_at,
    (ti.accepted_at IS NULL AND ti.expires_at > now()) AS is_valid
  FROM public.tenant_invites ti
  JOIN public.tenants t ON t.id = ti.tenant_id
  WHERE ti.token = p_token
  LIMIT 1
$$;

GRANT EXECUTE ON FUNCTION public.lookup_invite(text) TO anon, authenticated;


-- ── 6. Fortis tenant ────────────────────────────────────────────────
INSERT INTO public.tenants (id, name, slug, primary_color, notes)
VALUES (
  '00000000-0000-4000-8000-000000000001'::uuid,
  'The Fortis Agency',
  'fortis-agency',
  '#0F6E56',
  'Pilot tenant -- Fortis Stocks MVP. Indefinite access (access_granted_until IS NULL).'
)
ON CONFLICT (id) DO NOTHING;


-- ── 7. Bootstrap platform admin + Fortis members ────────────────────
-- If vibhora030@gmail.com exists in auth.users, make them a platform
-- admin AND a Fortis tenant admin. Anyone else with a confirmed email
-- becomes a regular Fortis member so they don't lose access on deploy.
DO $$
DECLARE
  fortis    uuid := '00000000-0000-4000-8000-000000000001'::uuid;
  admin_uid uuid;
BEGIN
  SELECT id INTO admin_uid
  FROM auth.users
  WHERE email = 'vibhora030@gmail.com'
  LIMIT 1;

  IF admin_uid IS NOT NULL THEN
    INSERT INTO public.platform_admins (user_id) VALUES (admin_uid)
    ON CONFLICT DO NOTHING;
    INSERT INTO public.tenant_members (tenant_id, user_id, role, accepted_at)
    VALUES (fortis, admin_uid, 'admin', now())
    ON CONFLICT DO NOTHING;
  END IF;

  -- Every other confirmed user -> Fortis member
  INSERT INTO public.tenant_members (tenant_id, user_id, role, accepted_at)
  SELECT fortis, id, 'member', now()
  FROM auth.users
  WHERE email_confirmed_at IS NOT NULL
    AND (admin_uid IS NULL OR id != admin_uid)
  ON CONFLICT DO NOTHING;
END $$;


-- ── 8. Add tenant_id to tables that don't have it yet ───────────────
ALTER TABLE public.portfolios                       ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE public.portfolio_holdings               ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE public.holdings_alerts                  ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE public.position_signals                 ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE public.portfolio_alert_summary          ADD COLUMN IF NOT EXISTS tenant_id uuid;
ALTER TABLE public.portfolio_rebalance_suggestions  ADD COLUMN IF NOT EXISTS tenant_id uuid;


-- ── 9. Backfill -- assign every existing row to Fortis ──────────────
UPDATE public.portfolios                      SET tenant_id = '00000000-0000-4000-8000-000000000001'::uuid WHERE tenant_id IS NULL;
UPDATE public.portfolio_holdings              SET tenant_id = '00000000-0000-4000-8000-000000000001'::uuid WHERE tenant_id IS NULL;
UPDATE public.holdings_alerts                 SET tenant_id = '00000000-0000-4000-8000-000000000001'::uuid WHERE tenant_id IS NULL;
UPDATE public.position_signals                SET tenant_id = '00000000-0000-4000-8000-000000000001'::uuid WHERE tenant_id IS NULL;
UPDATE public.portfolio_alert_summary         SET tenant_id = '00000000-0000-4000-8000-000000000001'::uuid WHERE tenant_id IS NULL;
UPDATE public.portfolio_rebalance_suggestions SET tenant_id = '00000000-0000-4000-8000-000000000001'::uuid WHERE tenant_id IS NULL;
UPDATE public.reports                         SET tenant_id = '00000000-0000-4000-8000-000000000001'::uuid WHERE tenant_id IS NULL;
UPDATE public.email_preferences               SET tenant_id = '00000000-0000-4000-8000-000000000001'::uuid WHERE tenant_id IS NULL;
UPDATE public.email_delivery_log              SET tenant_id = '00000000-0000-4000-8000-000000000001'::uuid WHERE tenant_id IS NULL;
UPDATE public.dashboard_events                SET tenant_id = '00000000-0000-4000-8000-000000000001'::uuid WHERE tenant_id IS NULL;
UPDATE public.user_action_summary             SET tenant_id = '00000000-0000-4000-8000-000000000001'::uuid WHERE tenant_id IS NULL;


-- ── 10. Set DEFAULT + NOT NULL on every tenant_id column ────────────
-- The DEFAULT keeps older insert code (report_composer, cron route)
-- working: any INSERT that omits tenant_id lands on Fortis. New tenant-
-- aware code paths will pass tenant_id explicitly.
ALTER TABLE public.portfolios                      ALTER COLUMN tenant_id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid, ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.portfolio_holdings              ALTER COLUMN tenant_id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid, ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.holdings_alerts                 ALTER COLUMN tenant_id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid, ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.position_signals                ALTER COLUMN tenant_id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid, ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.portfolio_alert_summary         ALTER COLUMN tenant_id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid, ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.portfolio_rebalance_suggestions ALTER COLUMN tenant_id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid, ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.reports                         ALTER COLUMN tenant_id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid, ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.email_preferences               ALTER COLUMN tenant_id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid, ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.email_delivery_log              ALTER COLUMN tenant_id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid, ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.dashboard_events                ALTER COLUMN tenant_id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid, ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE public.user_action_summary             ALTER COLUMN tenant_id SET DEFAULT '00000000-0000-4000-8000-000000000001'::uuid, ALTER COLUMN tenant_id SET NOT NULL;


-- ── 11. Tenant-table RLS ────────────────────────────────────────────
ALTER TABLE public.tenants          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenant_members   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenant_invites   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.platform_admins  ENABLE ROW LEVEL SECURITY;

-- tenants
DROP POLICY IF EXISTS tenants_member_read      ON public.tenants;
DROP POLICY IF EXISTS tenants_admin_update     ON public.tenants;
DROP POLICY IF EXISTS tenants_platform_all     ON public.tenants;
DROP POLICY IF EXISTS tenants_platform_insert  ON public.tenants;
CREATE POLICY tenants_member_read ON public.tenants
  FOR SELECT TO authenticated
  USING (public.is_tenant_member(id) OR public.is_platform_admin());
CREATE POLICY tenants_admin_update ON public.tenants
  FOR UPDATE TO authenticated
  USING (public.is_tenant_admin(id) OR public.is_platform_admin())
  WITH CHECK (public.is_tenant_admin(id) OR public.is_platform_admin());
CREATE POLICY tenants_platform_insert ON public.tenants
  FOR INSERT TO authenticated
  WITH CHECK (public.is_platform_admin());

-- tenant_members
DROP POLICY IF EXISTS tm_self_read        ON public.tenant_members;
DROP POLICY IF EXISTS tm_tenant_admin_all ON public.tenant_members;
DROP POLICY IF EXISTS tm_platform_all     ON public.tenant_members;
DROP POLICY IF EXISTS tm_admin_write      ON public.tenant_members;
CREATE POLICY tm_self_read ON public.tenant_members
  FOR SELECT TO authenticated
  USING (user_id = auth.uid()
         OR public.is_tenant_admin(tenant_id)
         OR public.is_platform_admin());
CREATE POLICY tm_admin_write ON public.tenant_members
  FOR ALL TO authenticated
  USING (public.is_tenant_admin(tenant_id) OR public.is_platform_admin())
  WITH CHECK (public.is_tenant_admin(tenant_id) OR public.is_platform_admin());

-- tenant_invites -- never exposed directly except via lookup_invite()
DROP POLICY IF EXISTS ti_admin_all ON public.tenant_invites;
CREATE POLICY ti_admin_all ON public.tenant_invites
  FOR ALL TO authenticated
  USING (public.is_tenant_admin(tenant_id) OR public.is_platform_admin())
  WITH CHECK (public.is_tenant_admin(tenant_id) OR public.is_platform_admin());

-- platform_admins -- only platform admins can read; only DB owner writes
DROP POLICY IF EXISTS pa_self_or_admin_read ON public.platform_admins;
CREATE POLICY pa_self_or_admin_read ON public.platform_admins
  FOR SELECT TO authenticated
  USING (user_id = auth.uid() OR public.is_platform_admin());


-- ── 12. Tenant-scoped RLS on the 11 data tables ─────────────────────
-- Pattern: drop every old owner_* / authenticated_read policy that
-- might still be around, enable RLS, install the new tenant policy.

-- 12a. Tenant-only tables (any member of the tenant can read/write)
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'portfolios','portfolio_holdings','holdings_alerts','position_signals',
    'portfolio_alert_summary','portfolio_rebalance_suggestions','reports'
  ]
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS "owner_all"            ON public.%I', t);
    EXECUTE format('DROP POLICY IF EXISTS "owner_only"           ON public.%I', t);
    EXECUTE format('DROP POLICY IF EXISTS "owner_read"           ON public.%I', t);
    EXECUTE format('DROP POLICY IF EXISTS "authenticated_read"   ON public.%I', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation       ON public.%I', t);
    EXECUTE format($f$
      CREATE POLICY tenant_isolation ON public.%I
      FOR ALL TO authenticated
      USING      (public.is_tenant_member(tenant_id))
      WITH CHECK (public.is_tenant_member(tenant_id))
    $f$, t);
  END LOOP;
END $$;

-- 12b. Tenant-AND-owner tables (per-user audit rows)
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'email_preferences','email_delivery_log','dashboard_events','user_action_summary'
  ]
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS "owner_all"               ON public.%I', t);
    EXECUTE format('DROP POLICY IF EXISTS "owner_only"              ON public.%I', t);
    EXECUTE format('DROP POLICY IF EXISTS "owner_read"              ON public.%I', t);
    EXECUTE format('DROP POLICY IF EXISTS "owner_insert"            ON public.%I', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation          ON public.%I', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_select   ON public.%I', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_insert   ON public.%I', t);
    -- SELECT: owner only AND tenant must match
    EXECUTE format($f$
      CREATE POLICY tenant_isolation_select ON public.%I
      FOR SELECT TO authenticated
      USING (user_id = auth.uid() AND public.is_tenant_member(tenant_id))
    $f$, t);
    -- INSERT: same gate
    EXECUTE format($f$
      CREATE POLICY tenant_isolation_insert ON public.%I
      FOR INSERT TO authenticated
      WITH CHECK (user_id = auth.uid() AND public.is_tenant_member(tenant_id))
    $f$, t);
  END LOOP;
END $$;


-- ── 13. Convenience indexes for the new join column ─────────────────
CREATE INDEX IF NOT EXISTS idx_portfolios_tenant         ON public.portfolios (tenant_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_tenant ON public.portfolio_holdings (tenant_id);
CREATE INDEX IF NOT EXISTS idx_holdings_alerts_tenant    ON public.holdings_alerts (tenant_id);
CREATE INDEX IF NOT EXISTS idx_position_signals_tenant   ON public.position_signals (tenant_id);
CREATE INDEX IF NOT EXISTS idx_reports_tenant            ON public.reports (tenant_id);
