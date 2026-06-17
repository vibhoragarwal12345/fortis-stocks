import Link from "next/link"

import { SignupForm } from "@/components/signup-form"

export const metadata = { title: "Create account — Christopher Edwards Financial Associates" }

// Open signup — anyone can create an account and is signed in instantly
// (accounts are created pre-confirmed; no confirmation email).

export default function SignupPage() {
  return (
    <div className="space-y-8">
      <div className="space-y-3">
        <h1 className="text-h2">Create account</h1>
        <p className="text-small text-muted-foreground">
          Free while in early access. You&rsquo;ll be in the terminal in
          seconds.
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
