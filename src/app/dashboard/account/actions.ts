"use server"

import { createClient } from "@/lib/supabase/server"

type ChangePasswordResult = { error: string } | { success: true }

/**
 * Authenticated password change for the signed-in user.
 *
 * Supabase's updateUser({ password }) operates on the *current session* and
 * does not re-check the existing password — so on its own it would let anyone
 * with a live session (e.g. an unlocked laptop) silently change the password.
 * We close that gap by re-verifying the current password with
 * signInWithPassword before calling updateUser. Both calls run against the
 * user's own session; nothing here uses the service role.
 */
export async function changePassword(formData: {
  currentPassword: string
  newPassword: string
}): Promise<ChangePasswordResult> {
  const { currentPassword, newPassword } = formData

  if (!newPassword || newPassword.length < 8) {
    return { error: "New password must be at least 8 characters." }
  }
  if (newPassword === currentPassword) {
    return { error: "New password must be different from your current one." }
  }

  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user?.email) {
    return { error: "Your session has expired. Sign in again to continue." }
  }

  // Re-verify the current password (current-session verification).
  const { error: verifyError } = await supabase.auth.signInWithPassword({
    email: user.email,
    password: currentPassword,
  })
  if (verifyError) {
    return { error: "Current password is incorrect." }
  }

  const { error: updateError } = await supabase.auth.updateUser({
    password: newPassword,
  })
  if (updateError) {
    return { error: updateError.message }
  }

  return { success: true }
}
