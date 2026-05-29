import Link from "next/link"
import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { getActiveTenantMember } from "@/lib/tenant"
import { themeFromTenant, tenantCssVars } from "@/lib/theme"
import { checkAccess } from "@/lib/permissions"
import { signout } from "./actions"

// Top-level nav for every /dashboard route. Active-link highlighting
// (usePathname) is deferred until we add a client-side wrapper -- a slim
// server-rendered nav keeps the first-byte fast and serves email-deep links.
const navItems = [
  { href: "/dashboard",              label: "Today" },
  { href: "/dashboard/focus-list",   label: "Focus list" },
  { href: "/dashboard/positions",    label: "Positions" },
  { href: "/dashboard/track-record", label: "Track record" },
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

  const membership = await getActiveTenantMember()
  const tenant = membership?.tenant ?? null
  const theme = themeFromTenant(tenant)
  const access = checkAccess(tenant)

  if (!access.ok) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="max-w-md rounded-lg border bg-card p-8 text-center shadow-sm">
          <h1 className="text-xl font-semibold tracking-tight">Access unavailable</h1>
          <p className="mt-3 text-sm text-muted-foreground">{access.message}</p>
          <form action={signout} className="mt-6">
            <button
              type="submit"
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              Sign out
            </button>
          </form>
        </div>
      </div>
    )
  }

  return (
    <div
      className="flex min-h-screen flex-col bg-background"
      style={tenantCssVars(theme)}
    >
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-3">
          <Link
            href="/dashboard"
            className="flex items-center gap-2 font-semibold tracking-tight text-foreground hover:text-foreground/80"
          >
            {theme.logoUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={theme.logoUrl}
                alt={theme.name}
                className="h-6 w-auto"
              />
            ) : (
              <span
                aria-hidden
                className="inline-block h-3 w-3 rounded-sm"
                style={{ backgroundColor: theme.primaryColor }}
              />
            )}
            <span>{theme.name}</span>
            <span className="text-muted-foreground"> · Intelligence</span>
          </Link>
          <nav className="hidden md:flex items-center gap-1">
            {navItems.map((item, idx) => (
              <Link
                key={`${item.label}-${idx}`}
                href={item.href}
                className={cn(
                  buttonVariants({ variant: "ghost", size: "sm" }),
                  "text-muted-foreground hover:text-foreground",
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
            <Link
              href="/dashboard/settings/branding"
              className={cn(
                buttonVariants({ variant: "ghost", size: "sm" }),
                "text-muted-foreground hover:text-foreground",
              )}
            >
              Settings
            </Link>
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
          Powered by <span className="font-semibold tracking-tight">{theme.name}</span>
          {" — institutional research for wealth advisors."}
        </div>
      </footer>
    </div>
  )
}
