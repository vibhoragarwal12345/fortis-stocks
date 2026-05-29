import type { Tenant } from "@/lib/tenant"

export type AccessVerdict =
  | { ok: true }
  | { ok: false; reason: "suspended" | "archived" | "expired" | "no_tenant"; message: string }

export function checkAccess(tenant: Tenant | null | undefined): AccessVerdict {
  if (!tenant) {
    return {
      ok: false,
      reason: "no_tenant",
      message:
        "Your account is not linked to a tenant. Contact vibhora030@gmail.com to be invited.",
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

export function canAccessFeature(
  tenant: Tenant | null | undefined,
  feature: string,
): boolean {
  if (!tenant) return false
  if (checkAccess(tenant).ok === false) return false
  const flag = tenant.feature_flags?.[feature]
  if (typeof flag === "boolean") return flag
  if (typeof flag === "number") return flag > 0
  return false
}

export function featureLimit(
  tenant: Tenant | null | undefined,
  feature: string,
): number {
  if (!tenant) return 0
  const flag = tenant.feature_flags?.[feature]
  return typeof flag === "number" ? flag : 0
}
