import Link from "next/link"

import { SignupForm } from "@/components/signup-form"

export const metadata = { title: "Create account — Fortis" }

// Open signup — anyone can create an account. New users join the Fortis
// workspace as members and confirm their email before first sign-in.

export default function SignupPage() {
  return (
    <div className="space-y-8">
      <div className="space-y-3">
        <h1 className="text-h2">Create account</h1>
        <p className="text-small text-muted-foreground">
          Free while in early access. You&rsquo;ll confirm your email before
          your first sign-in.
        </p>
      </div>

      <SignupForm />

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
