import Link from "next/link"

import { LoginForm } from "@/components/login-form"

export const metadata = { title: "Sign in — Fortis" }

interface LoginPageProps {
  searchParams: Promise<{ error?: string }>
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { error } = await searchParams

  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <h1 className="text-h2">Sign in</h1>
        <p className="text-small text-muted-foreground">
          Enter the email on your account.
        </p>
      </div>

      {error === "email_confirmation_failed" && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3.5 py-3 text-small text-destructive">
          Email confirmation failed. Try signing up again or request a new
          invite.
        </div>
      )}

      <LoginForm />

      <p className="text-small text-muted-foreground">
        Don&apos;t have an account?{" "}
        <Link
          href="/signup"
          className="font-medium text-foreground underline-offset-4 hover:underline"
        >
          Sign up
        </Link>
      </p>
    </div>
  )
}
