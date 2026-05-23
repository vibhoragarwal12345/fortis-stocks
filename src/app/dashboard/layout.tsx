import Link from "next/link"
import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { signout } from "./actions"

// Top-level nav for every /dashboard route. Active-link highlighting
// (usePathname) is deferred until we add a client-side wrapper -- a slim
// server-rendered nav keeps the first-byte fast and serves email-deep links.
const navItems = [
  { href: "/dashboard",              label: "Today" },
  { href: "/dashboard/focus-list",   label: "Focus list" },
  { href: "/dashboard/positions",    label: "Positions" },
  { href: "/dashboard/track-record", label: "Track record" },
  // Research is a ticker-deep-link page. Until the /dashboard/research
  // index lands, point the nav at the focus list (where tickers live).
  { href: "/dashboard/focus-list",   label: "Research" },
]

export default async function DashboardLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) redirect("/login")

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b bg-card">
        {/* primary row */}
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-3">
          <Link
            href="/dashboard"
            className="font-semibold tracking-tight text-foreground hover:text-foreground/80"
          >
            Fortis<span className="text-muted-foreground"> · Intelligence</span>
          </Link>
          <nav className="hidden md:flex items-center gap-1">
            {navItems.map((item, idx) => (
              <Link
                key={`${item.label}-${idx}`}
                href={item.href}
                className={cn(
                  buttonVariants({ variant: "ghost", size: "sm" }),
                  "text-muted-foreground hover:text-foreground"
                )}
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="flex items-center gap-3">
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
        {/* mobile nav row */}
        <nav
          aria-label="Mobile primary"
          className="md:hidden flex overflow-x-auto border-t px-4 py-2 gap-1 text-[13px] [&::-webkit-scrollbar]:hidden"
        >
          {navItems.map((item, idx) => (
            <Link
              key={`m-${item.label}-${idx}`}
              href={item.href}
              className="whitespace-nowrap px-3 py-1 text-muted-foreground hover:text-foreground"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </header>

      <main className="flex-1">{children}</main>

      <footer className="border-t bg-card">
        <div className="mx-auto max-w-7xl px-6 py-4 text-xs text-muted-foreground">
          Powered by <span className="font-semibold tracking-tight">Fortis</span>
          {" — institutional research for wealth advisors."}
        </div>
      </footer>
    </div>
  )
}
