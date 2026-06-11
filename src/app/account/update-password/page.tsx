import Link from "next/link"
import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { UpdatePasswordForm } from "@/components/update-password-form"
import { SiteFooter } from "@/components/site-footer"

export const metadata = { title: "Set new password — Fortis" }
export const dynamic = "force-dynamic"

export default async function UpdatePasswordPage() {
  // The recovery link puts the user through /auth/confirm (verifyOtp), which
  // creates a session. If there's no session, they got here some other way --
  // bounce them to /forgot-password so they can request a fresh link.
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) redirect("/forgot-password")

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <div className="flex flex-1 flex-col items-center px-6 py-16 md:py-24">
        <Link
          href="/"
          className="flex items-center gap-2 transition-premium hover:opacity-80"
        >
          <span
            aria-hidden
            className="inline-block h-2.5 w-2.5 rounded-sm bg-foreground"
          />
          <span className="text-caption uppercase tracking-[0.18em] text-foreground">
            Fortis
          </span>
        </Link>

        <div className="mt-14 w-full max-w-[400px] md:mt-20">
          <div className="space-y-8">
            <div className="space-y-3">
              <h1 className="text-h2">Set new password</h1>
              <p className="text-small text-muted-foreground">
                Choose a new password for{" "}
                <span className="font-medium text-foreground">
                  {user.email}
                </span>
                .
              </p>
            </div>

            <UpdatePasswordForm />
          </div>
        </div>
      </div>

      <SiteFooter />
    </div>
  )
}
