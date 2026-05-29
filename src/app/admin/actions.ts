"use server"

import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { createServiceClient } from "@/lib/supabase/service"
import { isPlatformAdmin } from "@/lib/tenant"

// Admin actions are used directly as <form action={...}> handlers so they
// must return void/Promise<void>. Failures throw — Next renders the error
// boundary, which is acceptable for a platform-admin-only surface.

async function requirePlatformAdmin() {
  const ok = await isPlatformAdmin()
  if (!ok) throw new Error("Not authorised")
}

function slugify(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "")
    .slice(0, 60) || "tenant"
}

const DEFAULT_FLAGS = {
  premarket_report: true,
  midday_report: true,
  close_report: true,
  multiple_portfolios: true,
  white_label: true,
  custom_email: true,
  max_portfolios: 50,
  max_holdings: 500,
}

export async function createTenant(formData: FormData): Promise<void> {
  await requirePlatformAdmin()
  const name = String(formData.get("name") || "").trim()
  const adminEmail = String(formData.get("admin_email") || "").trim().toLowerCase()
  if (!name || !adminEmail) {
    throw new Error("Both tenant name and admin email are required.")
  }

  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()

  const service = createServiceClient()
  let slug = slugify(name)
  for (let i = 0; i < 5; i++) {
    const { data: existing } = await service
      .from("tenants")
      .select("id")
      .eq("slug", slug)
      .maybeSingle()
    if (!existing) break
    slug = `${slugify(name)}-${Math.random().toString(36).slice(2, 6)}`
  }

  const { data: tenant, error: tenantErr } = await service
    .from("tenants")
    .insert({
      name,
      slug,
      feature_flags: DEFAULT_FLAGS,
      created_by_user_id: user?.id ?? null,
    })
    .select("id")
    .single()
  if (tenantErr || !tenant) {
    throw new Error(tenantErr?.message ?? "Failed to create tenant.")
  }

  const { error: inviteErr } = await service.from("tenant_invites").insert({
    tenant_id: tenant.id,
    email: adminEmail,
    role: "admin",
    invited_by: user?.id ?? null,
  })
  if (inviteErr) throw new Error(inviteErr.message)

  revalidatePath("/admin")
  redirect(`/admin/tenants/${tenant.id}`)
}

export async function updateTenant(formData: FormData): Promise<void> {
  await requirePlatformAdmin()
  const id = String(formData.get("id"))
  const access_status = String(formData.get("access_status") || "active")
  const granted_until_raw = String(formData.get("access_granted_until") || "").trim()
  const notes = String(formData.get("notes") || "")
  const flagsRaw = String(formData.get("feature_flags") || "")

  let feature_flags: unknown = undefined
  if (flagsRaw) {
    try {
      feature_flags = JSON.parse(flagsRaw)
    } catch {
      throw new Error("feature_flags must be valid JSON.")
    }
  }

  const patch: Record<string, unknown> = {
    access_status,
    access_granted_until: granted_until_raw ? granted_until_raw : null,
    notes,
  }
  if (feature_flags !== undefined) patch.feature_flags = feature_flags

  const service = createServiceClient()
  const { error } = await service.from("tenants").update(patch).eq("id", id)
  if (error) throw new Error(error.message)

  revalidatePath(`/admin/tenants/${id}`)
  revalidatePath("/admin")
}

export async function createInvite(formData: FormData): Promise<void> {
  await requirePlatformAdmin()
  const tenant_id = String(formData.get("tenant_id"))
  const email = String(formData.get("email") || "").trim().toLowerCase()
  const role = String(formData.get("role") || "member") as "admin" | "member"
  if (!tenant_id || !email) throw new Error("Email is required.")

  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()

  const service = createServiceClient()
  const { error } = await service.from("tenant_invites").insert({
    tenant_id,
    email,
    role,
    invited_by: user?.id ?? null,
  })
  if (error) throw new Error(error.message)

  revalidatePath(`/admin/tenants/${tenant_id}`)
}

export async function revokeInvite(formData: FormData): Promise<void> {
  await requirePlatformAdmin()
  const id = Number(formData.get("id"))
  const tenant_id = String(formData.get("tenant_id"))
  if (!id) throw new Error("Missing invite id.")
  const service = createServiceClient()
  const { error } = await service.from("tenant_invites").delete().eq("id", id)
  if (error) throw new Error(error.message)
  revalidatePath(`/admin/tenants/${tenant_id}`)
}
