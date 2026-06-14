import { createClient } from "@/lib/supabase/server"

export const FORTIS_TENANT_ID = "00000000-0000-4000-8000-000000000001"

export type FeatureFlags = {
  premarket_report: boolean
  midday_report: boolean
  close_report: boolean
  white_label: boolean
  [key: string]: boolean | number
}

export type Tenant = {
  id: string
  name: string
  slug: string
  logo_url: string | null
  primary_color: string
  email_from_name: string | null
  access_status: "active" | "suspended" | "archived"
  access_granted_until: string | null
  feature_flags: FeatureFlags
  notes: string | null
  created_at: string
}

export type TenantMember = {
  tenant_id: string
  user_id: string
  role: "admin" | "member"
}

export async function getActiveTenantMember(): Promise<{
  tenant: Tenant
  role: "admin" | "member"
} | null> {
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) return null

  const { data: member } = await supabase
    .from("tenant_members")
    .select("tenant_id, role, invited_at")
    .eq("user_id", user.id)
    .order("invited_at", { ascending: false })
    .limit(1)
    .maybeSingle()
  if (!member) return null

  const { data: tenant } = await supabase
    .from("tenants")
    .select("*")
    .eq("id", member.tenant_id)
    .maybeSingle()
  if (!tenant) return null
  return { tenant: tenant as Tenant, role: member.role as "admin" | "member" }
}

export async function isPlatformAdmin(): Promise<boolean> {
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) return false
  const { data } = await supabase
    .from("platform_admins")
    .select("user_id")
    .eq("user_id", user.id)
    .maybeSingle()
  return !!data
}
