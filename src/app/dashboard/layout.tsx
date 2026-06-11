import Link from "next/link"
import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { getActiveTenantMember } from "@/lib/tenant"
import { themeFromTenant, tenantCssVars } from "@/lib/theme"
import { checkAccess } from "@/lib/permissions"
import { AuroraField } from "@/components/ui/aurora-field"
import { CursorGlow } from "@/components/ui/cursor-glow"
import { NavLink } from "@/components/nav-link"
import { PageTransition } from "@/components/ui/page-transition"
import { TickerTape, type TapeItem } from "@/components/ui/ticker-tape"
import { SiteFooter } from "@/components/site-footer"
import { signout } from "./actions"

// The live tape under the header: the current shortlist's names with real
// prices from the latest scan, each linking into its research dossier.
async function getTapeItems(
  supabase: Awaited<ReturnType<typeof createClient>>,
): Promise<TapeItem[]> {
  const { data: state } = await supabase
    .from("scan_state")
    .select("latest_scan_id")
    .eq("id", 1)
    .maybeSingle()
  if (!state?.latest_scan_id) return []
  const { data } = await supabase
    .from("scan_results")
    .select("ticker, price, day_change_pct, rank")
    .eq("scan_id", state.latest_scan_id)
    .eq("advanced", true)
    .order("rank", { ascending: true })
    .limit(20)
  return (data ?? [])
    .filter((r) => r.price != null)
    .map((r) => ({
      ticker: r.ticker as string,
      price: Number(r.price).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }),
      change: Number(r.day_change_pct ?? 0),
      href: `/dashboard/research/${r.ticker}`,
    }))
}

// Top-level nav for every /dashboard route. NavLink lights the active
// section (exact match for Today, prefix for everything else).
const navItems = [
  { href: "/dashboard",              label: "Today", exact: true },
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
  const tapeItems = access.ok ? await getTapeItems(supabase) : []

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
      className="relative flex min-h-screen flex-col"
      style={tenantCssVars(theme)}
    >
      <CursorGlow />
      {/* Aurora pinned to the viewport — the gateway's light field rides
          along while scrolling. Data sits on solid cards, so legibility
          holds; the glass header blurs it as you scroll under. */}
      <div aria-hidden className="pointer-events-none fixed inset-0 -z-10">
        <AuroraField />
      </div>

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
                className="inline-block size-2 rounded-[2px]"
                style={{ backgroundColor: theme.primaryColor }}
              />
            )}
            <span className="text-[14px] font-semibold tracking-tight text-foreground">
              {theme.name}
            </span>
          </Link>

          {/* ── Primary nav (desktop) ───────────────────────────────── */}
          <nav className="hidden md:flex items-center gap-1">
            {navItems.map((item) => (
              <NavLink
                key={item.href}
                href={item.href}
                exact={item.exact}
                className="text-[13.5px]"
              >
                {item.label}
              </NavLink>
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
          className="md:hidden flex overflow-x-auto border-t border-border px-4 py-1.5 gap-1 text-[13px] [&::-webkit-scrollbar]:hidden"
        >
          {navItems.map((item) => (
            <NavLink
              key={`m-${item.href}`}
              href={item.href}
              exact={item.exact}
              className="py-2.5"
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>

      {/* Live tape — the latest shortlist on every dashboard page. */}
      {tapeItems.length > 0 && (
        <TickerTape items={tapeItems} className="border-t-0 bg-card/30" />
      )}

      <main className="flex-1">
        <PageTransition>{children}</PageTransition>
      </main>

      <SiteFooter brandName={theme.name} />
    </div>
  )
}
