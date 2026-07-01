import { createClient } from "@/lib/supabase/server"
import type { EmailOtpType } from "@supabase/supabase-js"
import { NextRequest, NextResponse } from "next/server"

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url)
  const token_hash = searchParams.get("token_hash")
  const type = searchParams.get("type") as EmailOtpType | null
  const code = searchParams.get("code")
  // Only ever redirect to a LOCAL path. An absolute ("https://evil.com") or
  // protocol-relative ("//evil.com") `next` would otherwise be an open
  // redirect once a valid OTP is presented.
  const nextParam = searchParams.get("next") ?? "/dashboard"
  const next =
    nextParam.startsWith("/") && !nextParam.startsWith("//")
      ? nextParam
      : "/dashboard"

  const supabase = await createClient()

  // PKCE flow — the default Supabase recovery/confirmation link delivers a
  // one-time `code`. Exchange it for a session so the user lands authenticated
  // (without this, recovery links drop the user on the site logged-out and the
  // update-password page bounces them away).
  if (code) {
    const { error } = await supabase.auth.exchangeCodeForSession(code)
    if (!error) {
      return NextResponse.redirect(new URL(next, origin))
    }
  }

  // Token-hash flow — used when the email template is customised to send
  // {{ .TokenHash }} (works cross-device, no PKCE code_verifier needed).
  if (token_hash && type) {
    const { error } = await supabase.auth.verifyOtp({ token_hash, type })
    if (!error) {
      return NextResponse.redirect(new URL(next, origin))
    }
  }

  return NextResponse.redirect(new URL("/login?error=email_confirmation_failed", origin))
}
