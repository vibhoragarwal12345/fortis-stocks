import Link from "next/link"

import { SignupForm } from "@/components/signup-form"
import { createClient } from "@/lib/supabase/server"

export const metadata = { title: "Create account — Fortis" }

type InviteRow = {
  tenant_id: string
  tenant_name: string
  email: string
  role: "admin" | "member"
  expires_at: string
  is_valid: boolean
}

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>
}) {
  const params = await searchParams
  const token = params.token?.trim()

  if (!token) {
    return (
      <div className="space-y-8">
        <div className="space-y-3">
          <h1 className="text-h2">Invite required</h1>
          <p className="text-small text-muted-foreground">
            Access is currently invite-only. Use the link from your invite
            email, or contact{" "}
            <a
              href="mailto:vibhora030@gmail.com"
              className="font-medium text-foreground underline-offset-4 hover:underline"
            >
              vibhora030@gmail.com
            </a>
            .
          </p>
        </div>
        <p className="text-small text-muted-foreground">
          Already have an account?{" "}
          <Link
            href="/login"
            className="font-medium text-foreground underline-offset-4 hover:underline"
          >
            Sign in
          </Link>
        </p>
      </div>
    )
  }

  const supabase = await createClient()
  const { data, error } = await supabase
    .rpc("lookup_invite", { p_token: token })
    .maybeSingle<InviteRow>()

  const invite = data
  const invalid = !!error || !invite || !invite.is_valid

  if (invalid) {
    return (
      <div className="space-y-8">
        <div className="space-y-3">
          <h1 className="text-h2">Invite link expired</h1>
          <p className="text-small text-muted-foreground">
            This invite link is no longer valid. Ask your inviter to send a
            fresh one.
          </p>
        </div>
        <p className="text-small text-muted-foreground">
          <Link
            href="/login"
            className="font-medium text-foreground underline-offset-4 hover:underline"
          >
            Sign in
          </Link>
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <div className="space-y-3">
        <h1 className="text-h2">Create account</h1>
        <p className="text-small text-muted-foreground">
          Joining{" "}
          <span className="font-medium text-foreground">{invite!.tenant_name}</span>{" "}
          as{" "}
          <span className="font-medium text-foreground">{invite!.email}</span>.
        </p>
      </div>

      <SignupForm token={token} inviteEmail={invite!.email} />

      <p className="text-small text-muted-foreground">
        Already have an account?{" "}
        <Link
          href="/login"
          className="font-medium text-foreground underline-offset-4 hover:underline"
        >
          Sign in
        </Link>
      </p>
    </div>
  )
}
