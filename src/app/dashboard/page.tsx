import Link from "next/link"
import {
  AlertTriangle,
  ArrowRight,
  Briefcase,
  Moon,
  Sun,
  Sunrise,
} from "lucide-react"

import { createClient } from "@/lib/supabase/server"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export const metadata = { title: "Today — Fortis" }

// ── Run-type metadata ──────────────────────────────────────────────────────
type RunType = "premarket" | "midday" | "close"
const RUN_TYPES: RunType[] = ["premarket", "midday", "close"]
const RUN_TYPE_LABEL: Record<RunType, string> = {
  premarket: "Pre-Market Brief",
  midday:    "Midday Brief",
  close:     "Close Brief",
}
const RUN_TYPE_TIME: Record<RunType, string> = {
  premarket: "6:00am ET",
  midday:    "12:30pm ET",
  close:     "5:00pm ET",
}
const RUN_TYPE_ICON = { premarket: Sunrise, midday: Sun, close: Moon } as const

// ── Tiny helpers ──────────────────────────────────────────────────────────
function greeting(now: Date) {
  const h = now.getHours()
  if (h < 12) return "Good morning"
  if (h < 17) return "Good afternoon"
  return "Good evening"
}

function displayName(user: {
  email?: string | null
  user_metadata?: Record<string, unknown>
}): string {
  const m = (user.user_metadata ?? {}) as Record<string, string | undefined>
  const raw = m.full_name || m.name || (user.email ?? "").split("@")[0] || "Advisor"
  return raw
    .split(" ")
    .map(s => s.charAt(0).toUpperCase() + s.slice(1))
    .join(" ")
}

function num(v: unknown): number | null {
  if (v == null) return null
  const n = typeof v === "number" ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

function verifyPct(v: unknown): number | null {
  const n = num(v)
  if (n == null) return null
  return n <= 1 ? Math.round(n * 100) : Math.round(n)
}

// ── Data shapes (lightly typed; Supabase returns rows untyped here) ───────
type ReportRow = {
  report_type: RunType
  a_grade_count?: number | null
  b_grade_count?: number | null
  c_grade_count?: number | null
  avg_verification_score?: number | null
}
type PickRow = {
  ticker: string
  conviction_grade?: string | null
  conviction_score_adjusted?: number | null
  catalyst_category?: string | null
  catalyst_description?: string | null
  composite_score?: number | null
  rank?: number | null
}
type PortfolioRow = { id: string; name?: string | null }
type SnapRow = {
  ticker: string
  price?: number | null
  gap_pct?: number | null
  snapshot_time?: string
}

// ── Page ──────────────────────────────────────────────────────────────────
export default async function DashboardHomePage() {
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  // Layout already redirects unauthenticated users; this is a defensive guard.
  if (!user) return null

  const now = new Date()
  const today = now.toISOString().slice(0, 10)
  const sevenDaysAgo = new Date(Date.now() - 7 * 86_400_000)
    .toISOString()
    .slice(0, 10)

  // Wave 1 — independent fetches.
  const [reportsRes, picksRes, portfoliosRes] = await Promise.all([
    supabase
      .from("reports")
      .select(
        "report_type,a_grade_count,b_grade_count,c_grade_count,avg_verification_score"
      )
      .eq("report_date", today),
    supabase
      .from("ranked_focus_list")
      .select(
        "ticker,conviction_grade,conviction_score_adjusted,catalyst_category,catalyst_description,composite_score,rank"
      )
      .eq("run_date", today)
      .eq("conviction_grade", "A")
      .order("rank")
      .limit(3),
    supabase.from("portfolios").select("id,name").eq("user_id", user.id),
  ])
  const reports = (reportsRes.data ?? []) as ReportRow[]
  const picks = (picksRes.data ?? []) as PickRow[]
  const portfolios = (portfoliosRes.data ?? []) as PortfolioRow[]

  // Wave 2 — derived fetches.
  const pids = portfolios.map(p => p.id)
  const tickers = picks.map(p => p.ticker).filter(Boolean) as string[]

  let holdings: { portfolio_id: string }[] = []
  let alerts: { portfolio_id: string; severity?: string | null }[] = []
  let snaps: SnapRow[] = []
  if (pids.length) {
    const [h, a] = await Promise.all([
      supabase
        .from("portfolio_holdings")
        .select("portfolio_id")
        .in("portfolio_id", pids),
      supabase
        .from("holdings_alerts")
        .select("portfolio_id,severity")
        .in("portfolio_id", pids)
        .gte("alert_date", sevenDaysAgo),
    ])
    holdings = (h.data ?? []) as typeof holdings
    alerts = (a.data ?? []) as typeof alerts
  }
  if (tickers.length) {
    const r = await supabase
      .from("market_snapshots")
      .select("ticker,price,gap_pct,snapshot_time")
      .in("ticker", tickers)
      .order("snapshot_time", { ascending: false })
    snaps = (r.data ?? []) as SnapRow[]
  }

  // Build latest snap per ticker (rows arrive in time-desc order).
  const snapByTicker: Record<string, SnapRow> = {}
  for (const s of snaps) if (!snapByTicker[s.ticker]) snapByTicker[s.ticker] = s

  const portfolioSummaries = portfolios.map(p => {
    const myAlerts = alerts.filter(a => a.portfolio_id === p.id)
    return {
      id: p.id,
      name: p.name ?? "Portfolio",
      holdings: holdings.filter(h => h.portfolio_id === p.id).length,
      alerts: myAlerts.length,
      critical: myAlerts.filter(a =>
        /critical|high/i.test(a.severity ?? "")
      ).length,
    }
  })

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 md:px-6 md:py-10 space-y-12">
      {/* ── Greeting ───────────────────────────────────────────── */}
      <header>
        <h1 className="font-heading text-2xl md:text-3xl font-semibold tracking-tight">
          {greeting(now)}, {displayName(user)}.
        </h1>
        <p className="mt-1 text-muted-foreground">
          Today is{" "}
          {now.toLocaleDateString("en-US", {
            weekday: "long",
            month: "long",
            day: "numeric",
            year: "numeric",
          })}
          .
        </p>
      </header>

      {/* ── Three report cards ─────────────────────────────────── */}
      <section aria-labelledby="briefs-heading">
        <h2
          id="briefs-heading"
          className="mb-4 text-xs font-semibold uppercase tracking-widest text-muted-foreground"
        >
          Today&apos;s briefs
        </h2>
        <div className="grid gap-4 md:grid-cols-3">
          {RUN_TYPES.map(t => {
            const r = reports.find(x => x.report_type === t)
            const Icon = RUN_TYPE_ICON[t]
            const a = r?.a_grade_count ?? 0
            const b = r?.b_grade_count ?? 0
            const c = r?.c_grade_count ?? 0
            const vsPct = verifyPct(r?.avg_verification_score)
            return (
              <Card key={t} size="sm">
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Icon className="size-4 text-muted-foreground" />
                      <CardTitle className="text-base">
                        {RUN_TYPE_LABEL[t]}
                      </CardTitle>
                    </div>
                    {r ? (
                      <Badge variant="success">Generated</Badge>
                    ) : (
                      <Badge variant="neutral">Awaiting</Badge>
                    )}
                  </div>
                  <CardDescription className="text-xs">
                    {RUN_TYPE_TIME[t]}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {r ? (
                    <p className="text-sm text-muted-foreground">
                      <span className="font-medium text-foreground">
                        {a} A
                      </span>{" "}
                      · <span>{b} B</span> · <span>{c} C</span>
                      {vsPct !== null && <> · verified {vsPct}%</>}
                    </p>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      Will be available at {RUN_TYPE_TIME[t]}.
                    </p>
                  )}
                  <div className="mt-3">
                    {r ? (
                      <Link
                        href={`/dashboard/reports/${today}/${t}`}
                        className={cn(
                          buttonVariants({ variant: "outline", size: "sm" }),
                          "w-full"
                        )}
                      >
                        Open report
                        <ArrowRight className="size-3.5" />
                      </Link>
                    ) : (
                      <button
                        type="button"
                        disabled
                        aria-disabled
                        className={cn(
                          buttonVariants({ variant: "outline", size: "sm" }),
                          "w-full opacity-50 cursor-not-allowed"
                        )}
                      >
                        Not yet generated
                      </button>
                    )}
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      </section>

      {/* ── Top 3 A picks ──────────────────────────────────────── */}
      <section aria-labelledby="picks-heading">
        <div className="mb-4 flex items-baseline justify-between gap-4">
          <h2
            id="picks-heading"
            className="text-xs font-semibold uppercase tracking-widest text-muted-foreground"
          >
            Today&apos;s top picks
          </h2>
          <Link
            href="/dashboard/focus-list"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            See full focus list
            <ArrowRight className="size-3" />
          </Link>
        </div>
        {picks.length === 0 ? (
          <Card size="sm">
            <CardContent className="py-6 text-sm text-muted-foreground">
              No A-grade picks for today yet. Picks are generated by the midday
              run at 12:30pm ET.
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 md:grid-cols-3">
            {picks.map(p => {
              const s = snapByTicker[p.ticker]
              const price = num(s?.price)
              const gap = num(s?.gap_pct)
              const cs = num(p.composite_score)
              return (
                <Card key={p.ticker} size="sm">
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <Link
                        href={`/dashboard/research/${p.ticker}`}
                        className="font-mono text-lg font-semibold tracking-wider hover:underline"
                      >
                        {p.ticker}
                      </Link>
                      <Badge variant="success">A</Badge>
                    </div>
                    <CardDescription className="text-xs">
                      {price !== null ? `$${price.toFixed(2)}` : "price n/a"}
                      {gap !== null && (
                        <>
                          {" · "}
                          <span
                            className={
                              gap >= 0
                                ? "text-emerald-700 dark:text-emerald-300"
                                : "text-red-700 dark:text-red-300"
                            }
                          >
                            {gap >= 0 ? "+" : ""}
                            {gap.toFixed(2)}%
                          </span>
                        </>
                      )}
                      {cs !== null && <> · score {cs.toFixed(1)}</>}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="text-sm">
                    {p.catalyst_category && (
                      <p className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
                        {p.catalyst_category}
                      </p>
                    )}
                    <p className="line-clamp-3 text-muted-foreground">
                      {p.catalyst_description ??
                        "Catalyst detail not yet attached."}
                    </p>
                  </CardContent>
                </Card>
              )
            })}
          </div>
        )}
      </section>

      {/* ── Portfolios at a glance ─────────────────────────────── */}
      <section aria-labelledby="portfolios-heading">
        <div className="mb-4 flex items-baseline justify-between gap-4">
          <h2
            id="portfolios-heading"
            className="text-xs font-semibold uppercase tracking-widest text-muted-foreground"
          >
            Portfolio at a glance
          </h2>
          <Link
            href="/dashboard/positions"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            Open positions
            <ArrowRight className="size-3" />
          </Link>
        </div>
        {portfolioSummaries.length === 0 ? (
          <Card size="sm">
            <CardContent className="py-6 text-sm text-muted-foreground">
              No portfolios linked to this account yet.
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {portfolioSummaries.map(p => (
              <Link
                key={p.id}
                href="/dashboard/positions"
                className="block group"
              >
                <Card
                  size="sm"
                  className="transition group-hover:ring-foreground/20"
                >
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Briefcase className="size-4 text-muted-foreground" />
                        <CardTitle className="text-base">{p.name}</CardTitle>
                      </div>
                      {p.critical > 0 && (
                        <Badge variant="destructive">
                          <AlertTriangle className="size-3 mr-1" />
                          {p.critical}
                        </Badge>
                      )}
                    </div>
                    <CardDescription className="text-xs">
                      {p.holdings} {p.holdings === 1 ? "holding" : "holdings"}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="text-sm text-muted-foreground">
                      {p.alerts === 0 ? (
                        "No active alerts."
                      ) : (
                        <>
                          {p.alerts} alert{p.alerts === 1 ? "" : "s"} in the
                          last week
                          {p.critical > 0 && (
                            <span className="text-red-700 dark:text-red-300">
                              {" — "}
                              {p.critical} critical/high
                            </span>
                          )}
                        </>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* ── Recent activity (placeholder for step 7.5) ─────────── */}
      <section aria-labelledby="activity-heading">
        <h2
          id="activity-heading"
          className="mb-4 text-xs font-semibold uppercase tracking-widest text-muted-foreground"
        >
          Recent activity
        </h2>
        <Card size="sm">
          <CardContent className="py-6 text-sm text-muted-foreground">
            <p className="font-medium text-foreground">
              Activity feed enabled in step 7.5
            </p>
            <p className="mt-1">
              Once feedback logging is in place, this section will show the last
              picks you opened, alerts you reviewed, and reports you shared.
            </p>
          </CardContent>
        </Card>
      </section>
    </div>
  )
}
