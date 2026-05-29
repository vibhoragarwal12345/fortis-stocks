"use server"

import { redirect } from "next/navigation"

import { createServiceClient } from "@/lib/supabase/service"

export async function updatePrefsByToken(formData: FormData) {
  const token = String(formData.get("token") ?? "")
  if (!token) redirect("/")

  const unsubAll = formData.get("unsub_all") === "1"
  const sb = createServiceClient()

  const patch = unsubAll
    ? {
        premarket_enabled: false,
        midday_enabled: false,
        close_enabled: false,
        updated_at: new Date().toISOString(),
      }
    : {
        premarket_enabled: formData.get("premarket_enabled") === "on",
        midday_enabled: formData.get("midday_enabled") === "on",
        close_enabled: formData.get("close_enabled") === "on",
        urgent_alerts_only: formData.get("urgent_alerts_only") === "on",
        updated_at: new Date().toISOString(),
      }

  await sb
    .from("email_preferences")
    .update(patch)
    .eq("unsubscribe_token", token)

  redirect(`/email/unsubscribe/${token}?saved=1`)
}
