"use server"

import { revalidatePath } from "next/cache"

import { createClient } from "@/lib/supabase/server"
import { createServiceClient } from "@/lib/supabase/service"
import { getActiveTenantMember } from "@/lib/tenant"

const BRANDING_BUCKET = "tenant-branding"

async function requireTenantAdmin() {
  const m = await getActiveTenantMember()
  if (!m) throw new Error("No tenant.")
  if (m.role !== "admin") throw new Error("Tenant admin role required.")
  return m
}

async function ensureBucket() {
  const service = createServiceClient()
  const { data: buckets } = await service.storage.listBuckets()
  if (!buckets?.some((b) => b.name === BRANDING_BUCKET)) {
    await service.storage.createBucket(BRANDING_BUCKET, { public: true })
  }
}

export async function updateBranding(formData: FormData): Promise<void> {
  const { tenant } = await requireTenantAdmin()
  const name = String(formData.get("name") || "").trim()
  const primary_color = String(formData.get("primary_color") || "").trim()
  const email_from_name = String(formData.get("email_from_name") || "").trim()
  const logo_url_input = String(formData.get("logo_url") || "").trim()

  const patch: Record<string, unknown> = {}
  if (name) patch.name = name
  if (primary_color) patch.primary_color = primary_color
  patch.email_from_name = email_from_name || null
  patch.logo_url = logo_url_input || null

  const file = formData.get("logo_file") as File | null
  if (file && typeof file === "object" && file.size > 0) {
    if (file.size > 2 * 1024 * 1024) {
      throw new Error("Logo file must be under 2 MB.")
    }
    await ensureBucket()
    const service = createServiceClient()
    const ext = (file.name.split(".").pop() || "png").toLowerCase().replace(/[^a-z0-9]/g, "")
    const key = `${tenant.id}/logo-${Date.now()}.${ext}`
    const buf = Buffer.from(await file.arrayBuffer())
    const { error: uploadErr } = await service.storage
      .from(BRANDING_BUCKET)
      .upload(key, buf, { contentType: file.type || "image/png", upsert: true })
    if (uploadErr) throw new Error(uploadErr.message)
    const { data: pub } = service.storage.from(BRANDING_BUCKET).getPublicUrl(key)
    patch.logo_url = pub.publicUrl
  }

  const supabase = await createClient()
  const { error } = await supabase.from("tenants").update(patch).eq("id", tenant.id)
  if (error) throw new Error(error.message)

  revalidatePath("/dashboard/settings/branding")
  revalidatePath("/dashboard")
}
