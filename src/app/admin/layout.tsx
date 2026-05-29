import Link from "next/link"
import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { isPlatformAdmin } from "@/lib/tenant"
import { buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { signout } from "@/app/dashboard/actions"

export const metadata = { title: "Admin — Fortis" }

export default async function AdminLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) redirect("/login")

  const allowed = await isPlatformAdmin()
  if (!allowed) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="max-w-md rounded-lg border bg-card p-8 text-center shadow-sm">
          <h1 className="text-xl font-semibold tracking-tight">Not authorised</h1>
          <p className="mt-3 text-sm text-muted-foreground">
            The /admin area is restricted to platform administrators.
          </p>
          <Link
            href="/dashboard"
            className={cn(
              buttonVariants({ variant: "outline", size: "sm" }),
              "mt-6",
            )}
          >
            Back to dashboard
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-3">
          <Link
            href="/admin"
            className="font-semibold tracking-tight text-foreground hover:text-foreground/80"
          >
            Fortis<span className="text-muted-foreground"> · Admin</span>
          </Link>
          <div className="flex items-center gap-3">
            <Link
              href="/dashboard"
              className={cn(
                buttonVariants({ variant: "ghost", size: "sm" }),
                "text-muted-foreground hover:text-foreground",
              )}
            >
              Dashboard
            </Link>
            <span className="hidden sm:inline text-xs text-muted-foreground truncate max-w-[180px]">
              {user.email}
            </span>
            <form action={signout}>
              <button
                type="submit"
                className={buttonVariants({ variant: "outline", size: "sm" })}
              >
                Sign out
              </button>
            </form>
          </div>
        </div>
      </header>
      <main className="flex-1">{children}</main>
    </div>
  )
}
