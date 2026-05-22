import Link from "next/link"
import { redirect } from "next/navigation"
import { createClient } from "@/lib/supabase/server"
import { buttonVariants } from "@/components/ui/button"
import { signout } from "./actions"

export const metadata = { title: "Dashboard — Fortis" }

export default async function DashboardPage() {
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()

  if (!user) {
    redirect("/login")
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b bg-card px-6 py-3">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <span className="text-base font-semibold tracking-tight">
            Fortis Stock Intelligence
          </span>
          <div className="flex items-center gap-4">
            <span className="text-sm text-muted-foreground">{user.email}</span>
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

      <main className="flex-1 px-6 py-10">
        <div className="mx-auto max-w-7xl">
          <h1 className="text-3xl font-bold">Welcome back</h1>
          <p className="mt-1 text-muted-foreground">{user.email}</p>
          <div className="mt-8 grid gap-4 sm:grid-cols-2">
            <Link
              href="/dashboard/track-record"
              className="group rounded-xl border bg-card px-6 py-8 text-left transition hover:border-foreground/20"
            >
              <div className="text-sm font-semibold tracking-tight">
                Track record
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                Forward performance of every A / B / C pick, benchmarked
                against SPY.
              </p>
            </Link>
            <Link
              href="/dashboard/positions"
              className="group rounded-xl border bg-card px-6 py-8 text-left transition hover:border-foreground/20"
            >
              <div className="text-sm font-semibold tracking-tight">
                Positions
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                Position-management signals for every tracked portfolio —
                color-coded review status, top priorities, and concentration alerts.
              </p>
            </Link>
          </div>
        </div>
      </main>
    </div>
  )
}
