import type { Tenant } from "@/lib/tenant"

export type TenantTheme = {
  name: string
  logoUrl: string | null
  primaryColor: string
  emailFromName: string
}

const DEFAULT_THEME: TenantTheme = {
  name: "Christopher Edwards Financial Associates",
  logoUrl: null,
  primaryColor: "#0F6E56",
  emailFromName: "Christopher Edwards Financial Associates",
}

export function themeFromTenant(tenant: Tenant | null | undefined): TenantTheme {
  if (!tenant) return DEFAULT_THEME
  return {
    name: tenant.name,
    logoUrl: tenant.logo_url,
    primaryColor: tenant.primary_color || DEFAULT_THEME.primaryColor,
    emailFromName: tenant.email_from_name || tenant.name || DEFAULT_THEME.emailFromName,
  }
}

/**
 * CSS variables to attach to a wrapper element so child Tailwind classes
 * (e.g., `bg-[color:var(--tenant-primary)]`) pick up the brand color.
 */
export function tenantCssVars(theme: TenantTheme): React.CSSProperties {
  return {
    ["--tenant-primary" as string]: theme.primaryColor,
  }
}
