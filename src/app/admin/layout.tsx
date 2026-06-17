import Link from "next/link"
import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { isPlatformAdmin } from "@/lib/tenant"
import { AuroraField } from "@/components/ui/aurora-field"
import { CursorGlow } from "@/components/ui/cursor-glow"
import { PageTransition } from "@/components/ui/page-transition"
import { SiteFooter } from "@/components/site-footer"
import { signout } from "@/app/dashboard/actions"

export const metadata = { title: "Admin — Christopher Edwards Financial Associates" }

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
      <div className="flex min-h-screen items-center justify-center bg-background px-6">
        <div className="max-w-md space-y-6 text-center">
          <h1 className="text-h2">Not authorised</h1>
          <p className="text-small text-muted-foreground">
            The /admin area is restricted to platform administrators.
          </p>
          <Link
            href="/dashboard"
            className="inline-flex h-9 items-center justify-center rounded-md border border-border bg-background px-4 text-[14px] font-medium text-foreground transition-premium hover:bg-secondary"
          >
            Back to dashboard
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="relative flex min-h-screen flex-col">
      <CursorGlow />
      <div aria-hidden className="pointer-events-none fixed inset-0 -z-10">
        <AuroraField />
      </div>

      <header className="sticky top-0 z-30 border-b border-border bg-background/85 backdrop-blur-md backdrop-saturate-150">
        <div className="mx-auto flex h-14 max-w-[1280px] items-center justify-between gap-6 px-6 md:px-10">
          <Link
            href="/admin"
            className="flex shrink-0 items-center gap-2 transition-premium hover:opacity-80"
          >
            <span aria-hidden className="size-2 rounded-[2px] bg-highlight" />
            <span className="text-[13px] font-semibold uppercase tracking-[0.22em]">
              Christopher Edwards Financial Associates
            </span>
            <span className="text-eyebrow">
              Admin
            </span>
          </Link>
          <div className="flex shrink-0 items-center gap-1">
            <Link
              href="/dashboard"
              className="rounded-md px-3 py-1.5 text-[13.5px] text-muted-foreground transition-premium hover:text-foreground"
            >
              Dashboard
            </Link>
            <form action={signout}>
              <button
                type="submit"
                className="rounded-md px-3 py-1.5 text-[13.5px] text-muted-foreground transition-premium hover:text-foreground"
              >
                Sign out
              </button>
            </form>
          </div>
        </div>
      </header>
      <main className="flex-1">
        <PageTransition>{children}</PageTransition>
      </main>
      <SiteFooter />
    </div>
  )
}
