import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { UpdatePasswordForm } from "@/components/update-password-form"

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
    <div className="mx-auto max-w-md px-4 py-16">
      <Card>
        <CardHeader>
          <CardTitle>Set new password</CardTitle>
          <CardDescription>
            Choose a new password for {user.email}.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <UpdatePasswordForm />
        </CardContent>
      </Card>
    </div>
  )
}
