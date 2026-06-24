import Link from "next/link"
import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { AuroraField } from "@/components/ui/aurora-field"
import { CursorGlow } from "@/components/ui/cursor-glow"
import { UpdatePasswordForm } from "@/components/update-password-form"
import { SiteFooter } from "@/components/site-footer"

export const metadata = { title: "Set new password · Christopher Edwards Financial Associates" }
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
    <div className="relative flex min-h-svh flex-col items-center overflow-hidden bg-background px-6 py-10 md:py-14">
      <CursorGlow />
      <AuroraField />

      <Link
        href="/"
        className="relative flex items-center gap-2.5 transition-premium hover:opacity-80"
      >
        <span aria-hidden className="size-2 rounded-[2px] bg-highlight" />
        <span className="text-[13px] font-semibold uppercase tracking-[0.22em]">
          Christopher Edwards Financial Associates
        </span>
      </Link>

      <div className="relative mt-12 w-full max-w-[420px] md:mt-16">
        <div className="rounded-2xl border border-border bg-card/75 p-8 shadow-[var(--shadow-lg)] backdrop-blur-xl md:p-10">
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

      <div className="relative mt-auto w-full pt-12">
        <SiteFooter />
      </div>
    </div>
  )
}
