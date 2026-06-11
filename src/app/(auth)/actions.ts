"use server"

import { headers } from "next/headers"
import { createClient } from "@/lib/supabase/server"
import { createServiceClient } from "@/lib/supabase/service"
import { FORTIS_TENANT_ID } from "@/lib/tenant"
import { redirect } from "next/navigation"

async function siteOrigin(): Promise<string> {
  const env = process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/$/, "")
  if (env) return env
  const h = await headers()
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost:3000"
  const proto = h.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https")
  return `${proto}://${host}`
}

export async function login(formData: { email: string; password: string }) {
  const supabase = await createClient()

  const { error } = await supabase.auth.signInWithPassword({
    email: formData.email,
    password: formData.password,
  })

  if (error) {
    return { error: error.message }
  }

  redirect("/dashboard")
}

/**
 * Open signup. Anyone can create an account; new users join the Fortis
 * tenant as members. (The invite-token gate was removed for the
 * friends-and-family launch — admin-panel invites still work for other
 * tenants, they're just no longer required.)
 */
export async function signup(formData: { email: string; password: string }) {
  const supabase = await createClient()

  const { data: signUpData, error: signUpErr } = await supabase.auth.signUp({
    email: formData.email,
    password: formData.password,
  })

  if (signUpErr) return { error: signUpErr.message }

  const newUserId = signUpData.user?.id
  if (!newUserId) {
    return { error: "Sign-up succeeded but no user id was returned." }
  }

  // Service role: link the new user to the default tenant. Done server-side
  // before email confirmation completes -- otherwise the user would land in
  // /login with no tenant.
  const service = createServiceClient()
  const { error: memberErr } = await service
    .from("tenant_members")
    .upsert(
      {
        tenant_id: FORTIS_TENANT_ID,
        user_id: newUserId,
        role: "member",
        invited_by: null,
        accepted_at: new Date().toISOString(),
      },
      { onConflict: "tenant_id,user_id" },
    )
  if (memberErr) return { error: memberErr.message }

  redirect("/check-email")
}

/**
 * Sends a password-recovery email. Never reveals whether the address exists
 * (always returns ok) -- callers redirect to a generic "check your email"
 * message regardless.
 */
export async function requestPasswordReset(formData: { email: string }) {
  const email = formData.email.trim().toLowerCase()
  if (!email) return { error: "Email is required." }

  const supabase = await createClient()
  const origin = await siteOrigin()
  const redirectTo = `${origin}/auth/confirm?next=/account/update-password`
  const { error } = await supabase.auth.resetPasswordForEmail(email, {
    redirectTo,
  })
  if (error) return { error: error.message }
  return { ok: true }
}

/**
 * Sets a new password for the currently authenticated user. The recovery
 * link puts them through /auth/confirm (verifyOtp with type=recovery), which
 * creates a session, so updateUser() works without re-auth.
 */
export async function updatePassword(formData: { password: string }) {
  const password = formData.password
  if (!password || password.length < 8) {
    return { error: "Password must be at least 8 characters." }
  }
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) return { error: "You must use a fresh recovery link to set a new password." }

  const { error } = await supabase.auth.updateUser({ password })
  if (error) return { error: error.message }
  redirect("/dashboard")
}

