import Link from "next/link"
import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { getActiveTenantMember } from "@/lib/tenant"
import { themeFromTenant, tenantCssVars } from "@/lib/theme"
import { checkAccess } from "@/lib/permissions"
import { PageTransition } from "@/components/ui/page-transition"
import { SiteFooter } from "@/components/site-footer"
import { signout } from "./actions"

// Top-level nav for every /dashboard route. Active-link highlighting is
// deferred to a follow-up; a slim server-rendered nav keeps first-byte fast.
const navItems = [
  { href: "/dashboard",              label: "Today" },
  { href: "/dashboard/focus-list",   label: "Focus list" },
  { href: "/dashboard/emerging",     label: "Emerging" },
  { href: "/dashboard/scan-history", label: "Scan history" },
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
      <div className="flex min-h-screen items-center justify-center bg-background px-6">
        <div className="max-w-md space-y-6 text-center">
          <h1 className="text-h2">Access unavailable</h1>
          <p className="text-small text-muted-foreground">{access.message}</p>
          <form action={signout}>
            <button
              type="submit"
              className="inline-flex h-9 items-center justify-center rounded-md border border-border bg-background px-4 text-[14px] font-medium text-foreground transition-premium hover:bg-secondary"
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
      <header className="sticky top-0 z-30 border-b border-border bg-background/85 backdrop-blur-md backdrop-saturate-150">
        <div className="mx-auto flex h-14 max-w-[1280px] items-center justify-between gap-6 px-6 md:px-10">
          {/* ── Wordmark ─────────────────────────────────────────────── */}
          <Link
            href="/dashboard"
            className="flex shrink-0 items-center gap-2 transition-premium hover:opacity-80"
          >
            {theme.logoUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={theme.logoUrl}
                alt={theme.name}
                className="h-5 w-auto"
              />
            ) : (
              <span
                aria-hidden
                className="inline-block h-2.5 w-2.5 rounded-sm"
                style={{ backgroundColor: theme.primaryColor }}
              />
            )}
            <span className="text-[14px] font-semibold tracking-tight text-foreground">
              {theme.name}
            </span>
          </Link>

          {/* ── Primary nav (desktop) ───────────────────────────────── */}
          <nav className="hidden md:flex items-center gap-1">
            {navItems.map((item, idx) => (
              <Link
                key={`${item.label}-${idx}`}
                href={item.href}
                className="rounded-md px-3 py-1.5 text-[13.5px] text-muted-foreground transition-premium hover:text-foreground"
              >
                {item.label}
              </Link>
            ))}
          </nav>

          {/* ── Account ─────────────────────────────────────────────── */}
          <div className="flex shrink-0 items-center gap-1">
            <Link
              href="/dashboard/account"
              className="hidden rounded-md px-3 py-1.5 text-[13.5px] text-muted-foreground transition-premium hover:text-foreground sm:inline-block"
            >
              Account
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

        {/* ── Mobile horizontal nav ───────────────────────────────── */}
        <nav
          aria-label="Mobile primary"
          className="md:hidden flex overflow-x-auto border-t border-border px-4 py-2 gap-1 text-[13px] [&::-webkit-scrollbar]:hidden"
        >
          {navItems.map((item, idx) => (
            <Link
              key={`m-${item.label}-${idx}`}
              href={item.href}
              className="whitespace-nowrap rounded-md px-3 py-1.5 text-muted-foreground transition-premium hover:text-foreground"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </header>

      <main className="flex-1">
        <PageTransition>{children}</PageTransition>
      </main>

      <SiteFooter brandName={theme.name} />
    </div>
  )
}
