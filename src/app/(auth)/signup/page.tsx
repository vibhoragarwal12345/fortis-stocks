import Link from "next/link"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
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
      <Card>
        <CardHeader>
          <CardTitle>Invite required</CardTitle>
          <CardDescription>
            Access is currently invite-only. If you were invited, use the link
            from your invite email.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Need access? Contact{" "}
          <a
            href="mailto:vibhora030@gmail.com"
            className="font-medium text-foreground underline-offset-4 hover:underline"
          >
            vibhora030@gmail.com
          </a>
          .
        </CardContent>
        <CardFooter className="justify-center text-sm text-muted-foreground">
          <Link
            href="/login"
            className="font-medium text-foreground underline-offset-4 hover:underline"
          >
            Sign in instead
          </Link>
        </CardFooter>
      </Card>
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
      <Card>
        <CardHeader>
          <CardTitle>Invite link not valid</CardTitle>
          <CardDescription>
            This invite link is invalid or has expired. Ask your inviter to
            send a fresh link.
          </CardDescription>
        </CardHeader>
        <CardFooter className="justify-center text-sm text-muted-foreground">
          <Link
            href="/login"
            className="font-medium text-foreground underline-offset-4 hover:underline"
          >
            Sign in
          </Link>
        </CardFooter>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Create account</CardTitle>
        <CardDescription>
          Joining <span className="font-medium text-foreground">{invite!.tenant_name}</span> as{" "}
          <span className="font-medium text-foreground">{invite!.email}</span>.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <SignupForm token={token} inviteEmail={invite!.email} />
      </CardContent>
      <CardFooter className="justify-center text-sm text-muted-foreground">
        Already have an account?&nbsp;
        <Link
          href="/login"
          className="font-medium text-foreground underline-offset-4 hover:underline"
        >
          Sign in
        </Link>
      </CardFooter>
    </Card>
  )
}
