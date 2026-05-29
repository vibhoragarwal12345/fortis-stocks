"use server"

import { headers } from "next/headers"
import { createClient } from "@/lib/supabase/server"
import { createServiceClient } from "@/lib/supabase/service"
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
 * Invite-only signup. Validates `token` against tenant_invites (anonymously,
 * via lookup_invite RPC), creates the auth user, and atomically links them
 * to the tenant. Email must match the invite (case-insensitive).
 */
export async function signup(formData: {
  email: string
  password: string
  token: string
}) {
  const token = formData.token?.trim()
  if (!token) {
    return { error: "Signup is invite-only. An invite link is required." }
  }

  type InviteRow = {
    tenant_id: string
    tenant_name: string
    email: string
    role: "admin" | "member"
    expires_at: string
    is_valid: boolean
  }

  const supabase = await createClient()
  const { data: invite, error: lookupErr } = await supabase
    .rpc("lookup_invite", { p_token: token })
    .maybeSingle<InviteRow>()

  if (lookupErr) return { error: lookupErr.message }
  if (!invite || !invite.is_valid) {
    return { error: "This invite link is invalid or has expired." }
  }
  if (
    formData.email.trim().toLowerCase() !==
    String(invite.email).trim().toLowerCase()
  ) {
    return { error: "Email must match the address the invite was sent to." }
  }

  const { data: signUpData, error: signUpErr } = await supabase.auth.signUp({
    email: formData.email,
    password: formData.password,
  })

  if (signUpErr) return { error: signUpErr.message }

  const newUserId = signUpData.user?.id
  if (!newUserId) {
    return { error: "Sign-up succeeded but no user id was returned." }
  }

  // Service role: link the new user to the tenant and mark invite consumed.
  // We do this server-side regardless of whether email confirmation has
  // completed -- otherwise the user would land in /login with no tenant.
  const service = createServiceClient()
  const { error: memberErr } = await service
    .from("tenant_members")
    .upsert(
      {
        tenant_id: invite.tenant_id as string,
        user_id: newUserId,
        role: invite.role as "admin" | "member",
        invited_by: null,
        accepted_at: new Date().toISOString(),
      },
      { onConflict: "tenant_id,user_id" },
    )
  if (memberErr) return { error: memberErr.message }

  await service
    .from("tenant_invites")
    .update({
      accepted_at: new Date().toISOString(),
      accepted_by_user_id: newUserId,
    })
    .eq("token", token)

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

