import Link from "next/link"

import { ForgotPasswordForm } from "@/components/forgot-password-form"

export const metadata = { title: "Reset password · Christopher Edwards Financial Associates" }

export default function ForgotPasswordPage() {
  return (
    <div className="space-y-8">
      <div className="space-y-3">
        <h1 className="text-h2">Reset password</h1>
        <p className="text-small text-muted-foreground">
          Enter the email on your account. We&rsquo;ll send a link to set a
          new password.
        </p>
      </div>

      <ForgotPasswordForm />

      <p className="text-small text-muted-foreground">
        Remembered it?{" "}
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
