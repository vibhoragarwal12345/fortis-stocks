import type { Tenant } from "@/lib/tenant"

// The single owner account. Operational/internal surfaces (e.g. the scan
// history page) are visible ONLY to this email during the pilot -- not to
// client tenants. Kept as a literal (not the platform_admins table) so the
// gate is exact and works even before that table is seeded. The match is on
// the verified Supabase auth email and is case-insensitive.
export const OWNER_EMAIL = "vibhora030@gmail.com"

export function isOwner(
  user: { email?: string | null } | null | undefined,
): boolean {
  return !!user?.email && user.email.toLowerCase() === OWNER_EMAIL
}

export type AccessVerdict =
  | { ok: true }
  | { ok: false; reason: "suspended" | "archived" | "expired" | "no_tenant"; message: string }

export function checkAccess(tenant: Tenant | null | undefined): AccessVerdict {
  if (!tenant) {
    return {
      ok: false,
      reason: "no_tenant",
      message:
        "Your account is not linked to a workspace. Contact vibhora030@gmail.com for access.",
    }
  }
  if (tenant.access_status === "suspended") {
    return {
      ok: false,
      reason: "suspended",
      message:
        "Your access is currently suspended. Contact vibhora030@gmail.com to restore access.",
    }
  }
  if (tenant.access_status === "archived") {
    return {
      ok: false,
      reason: "archived",
      message:
        "Your access has been archived. Contact vibhora030@gmail.com if you need to be reactivated.",
    }
  }
  if (tenant.access_granted_until) {
    const until = new Date(tenant.access_granted_until).getTime()
    if (Number.isFinite(until) && until < Date.now()) {
      return {
        ok: false,
        reason: "expired",
        message:
          "Your pilot access has ended. Contact vibhora030@gmail.com to extend access.",
      }
    }
  }
  return { ok: true }
}
