import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { Reveal } from "@/components/ui/reveal"
import { ChangePasswordForm } from "@/components/change-password-form"

export const metadata = { title: "Account — Christopher Edwards Financial Associates" }
export const dynamic = "force-dynamic"

export default async function AccountPage() {
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) redirect("/login")

  return (
    <div className="mx-auto max-w-[560px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <p className="text-eyebrow">Account</p>
        <h1 className="text-h1">Your account.</h1>
        <p className="text-body-lg text-muted-foreground">
          Signed in as{" "}
          <span className="font-medium text-foreground">{user.email}</span>.
        </p>
      </Reveal>

      <Reveal>
        <section className="space-y-6">
          <div className="space-y-1.5">
            <h2 className="text-h3">Change password</h2>
            <p className="text-small text-muted-foreground">
              Confirm your current password, then choose a new one (at least
              8 characters).
            </p>
          </div>
          <ChangePasswordForm />
        </section>
      </Reveal>
    </div>
  )
}
