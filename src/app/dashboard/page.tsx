import Link from "next/link"
import { ArrowRight, Moon, Sun, Sunrise } from "lucide-react"

import { createClient } from "@/lib/supabase/server"
import { trackEvent } from "@/lib/track"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

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

function prettyEvent(type: string): string {
  return type
    .replace(/_/g, " ")
    .replace(/\bdashboard\b/i, "")
    .replace(/\bopened\b/i, "opened")
    .trim()
    .replace(/^\w/, (c) => c.toUpperCase())
}

function timeAgo(d: Date): string {
  const ms = Date.now() - d.getTime()
  const m = Math.round(ms / 60000)
  if (m < 1) return "just now"
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  const dd = Math.round(h / 24)
  return `${dd}d ago`
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

  // Step 7.5 -- log page view. Fire-and-forget; await so the 5s dedup
  // bucket sees a stable occurred_at, but failures never break render.
  await trackEvent("dashboard_today_opened")

  const now = new Date()
  const today = now.toISOString().slice(0, 10)
  const sevenDaysAgo = new Date(Date.now() - 7 * 86_400_000)
    .toISOString()
    .slice(0, 10)

  // Wave 1 — independent fetches.
  // If today's pipeline hasn't run yet (weekends, holidays, pre-6am ET),
  // we also pull the latest run_date that DOES have data so we can show
  // the most recent picks instead of an empty dashboard.
  const [reportsRes, picksRes, portfoliosRes, latestRunRes, latestEventsRes] = await Promise.all([
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
    supabase
      .from("ranked_focus_list")
      .select("run_date")
      .order("run_date", { ascending: false })
      .limit(1)
      .maybeSingle(),
    supabase
      .from("dashboard_events")
      .select("event_type,event_data,occurred_at")
      .eq("user_id", user.id)
      .order("occurred_at", { ascending: false })
      .limit(6),
  ])
  const reports = (reportsRes.data ?? []) as ReportRow[]
  let picks = (picksRes.data ?? []) as PickRow[]
  const portfolios = (portfoliosRes.data ?? []) as PortfolioRow[]
  const latestRunDate = (latestRunRes.data?.run_date as string | undefined) ?? null
  const showingFallbackPicks = picks.length === 0 && latestRunDate && latestRunDate !== today
  if (showingFallbackPicks) {
    const fallback = await supabase
      .from("ranked_focus_list")
      .select(
        "ticker,conviction_grade,conviction_score_adjusted,catalyst_category,catalyst_description,composite_score,rank"
      )
      .eq("run_date", latestRunDate)
      .eq("conviction_grade", "A")
      .order("rank")
      .limit(3)
    picks = (fallback.data ?? []) as PickRow[]
  }
  const recentEvents = (latestEventsRes.data ?? []) as Array<{
    event_type: string
    event_data: Record<string, unknown>
    occurred_at: string
  }>

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

  const dateLine = now.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  })

  return (
    <div className="mx-auto max-w-[1280px] space-y-20 px-6 py-14 md:space-y-24 md:px-10 md:py-16">
      {/* ── Greeting ───────────────────────────────────────────── */}
      <Reveal as="header" className="space-y-3">
        <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
          {dateLine}
        </p>
        <h1 className="text-h1">
          {greeting(now)}, {displayName(user)}.
        </h1>
      </Reveal>

      {/* ── Three report cards ─────────────────────────────────── */}
      <section aria-labelledby="briefs-heading" className="space-y-6">
        <Reveal>
          <h2
            id="briefs-heading"
            className="text-caption uppercase tracking-[0.18em] text-muted-foreground"
          >
            Today&apos;s briefs
          </h2>
        </Reveal>
        <div className="grid gap-5 md:grid-cols-3">
          {RUN_TYPES.map((t, idx) => {
            const r = reports.find(x => x.report_type === t)
            const Icon = RUN_TYPE_ICON[t]
            const a = r?.a_grade_count ?? 0
            const b = r?.b_grade_count ?? 0
            const c = r?.c_grade_count ?? 0
            const vsPct = verifyPct(r?.avg_verification_score)
            const inner = (
              <Card
                size="sm"
                className="h-full transition-premium duration-300 group-hover:-translate-y-0.5 group-hover:shadow-[var(--shadow-md)]"
              >
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Icon className="size-4" />
                      <CardTitle className="text-[15px]">
                        {RUN_TYPE_LABEL[t]}
                      </CardTitle>
                    </div>
                    <span
                      className={
                        r
                          ? "text-caption uppercase tracking-[0.14em] text-foreground"
                          : "text-caption uppercase tracking-[0.14em] text-muted-foreground"
                      }
                    >
                      {r ? "Generated" : "Awaiting"}
                    </span>
                  </div>
                  <p className="text-caption">{RUN_TYPE_TIME[t]}</p>
                </CardHeader>
                <CardContent className="space-y-4">
                  {r ? (
                    <p className="text-small text-muted-foreground">
                      <span className="font-medium text-foreground tabular-nums">
                        {a} A
                      </span>{" "}
                      · <span className="tabular-nums">{b} B</span> ·{" "}
                      <span className="tabular-nums">{c} C</span>
                      {vsPct !== null && (
                        <> · verified {vsPct}%</>
                      )}
                    </p>
                  ) : (
                    <p className="text-small text-muted-foreground">
                      Daily briefs run weekdays. Next one at {RUN_TYPE_TIME[t]}.
                    </p>
                  )}
                  <div className="flex items-center text-small">
                    {r ? (
                      <span className="inline-flex items-center gap-1 font-medium text-foreground">
                        Open report
                        <ArrowRight className="size-3.5" />
                      </span>
                    ) : (
                      <span className="text-muted-foreground">
                        Not yet generated
                      </span>
                    )}
                  </div>
                </CardContent>
              </Card>
            )
            return (
              <Reveal key={t} delay={idx * 80}>
                {r ? (
                  <Link
                    href={`/dashboard/reports/${today}/${t}`}
                    className="group block h-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-xl"
                  >
                    {inner}
                  </Link>
                ) : (
                  <div className="group h-full opacity-90">{inner}</div>
                )}
              </Reveal>
            )
          })}
        </div>
      </section>

      {/* ── Top 3 A picks ──────────────────────────────────────── */}
      <section aria-labelledby="picks-heading" className="space-y-6">
        <Reveal>
          <div className="flex items-baseline justify-between gap-4">
            <h2
              id="picks-heading"
              className="text-caption uppercase tracking-[0.18em] text-muted-foreground"
            >
              Today&apos;s top picks
            </h2>
            <Link
              href="/dashboard/focus-list"
              className="inline-flex items-center gap-1 text-caption transition-premium hover:text-foreground"
            >
              See full focus list
              <ArrowRight className="size-3" />
            </Link>
          </div>
        </Reveal>
        {showingFallbackPicks && (
          <Reveal>
            <div className="rounded-md border border-border bg-secondary/40 px-3.5 py-2.5 text-caption text-muted-foreground">
              Today&apos;s pipeline hasn&apos;t run yet — showing the latest
              A-grade picks from {latestRunDate}.
            </div>
          </Reveal>
        )}
        {picks.length === 0 ? (
          <Reveal>
            <Card size="sm">
              <CardContent className="py-8 text-small text-muted-foreground">
                No A-grade picks on file yet. Picks are generated by the
                midday run at 12:30pm ET on weekdays.
              </CardContent>
            </Card>
          </Reveal>
        ) : (
          <div className="grid gap-5 md:grid-cols-3">
            {picks.map((p, idx) => {
              const s = snapByTicker[p.ticker]
              const price = num(s?.price)
              const gap = num(s?.gap_pct)
              const cs = num(p.composite_score)
              return (
                <Reveal key={p.ticker} delay={idx * 80}>
                  <Link
                    href={`/dashboard/research/${p.ticker}`}
                    className="group block h-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-xl"
                  >
                    <Card
                      size="sm"
                      className="h-full transition-premium duration-300 group-hover:-translate-y-0.5 group-hover:shadow-[var(--shadow-md)]"
                    >
                      <CardHeader>
                        <div className="flex items-baseline justify-between">
                          <span className="font-mono text-[20px] font-semibold tracking-tight">
                            {p.ticker}
                          </span>
                          <span className="text-caption uppercase tracking-[0.14em] text-foreground">
                            A
                          </span>
                        </div>
                        <p className="text-caption tabular-nums">
                          {price !== null ? `$${price.toFixed(2)}` : "price n/a"}
                          {gap !== null && (
                            <>
                              {" · "}
                              <span
                                className={
                                  gap >= 0
                                    ? "text-foreground"
                                    : "text-muted-foreground"
                                }
                              >
                                {gap >= 0 ? "+" : ""}
                                {gap.toFixed(2)}%
                              </span>
                            </>
                          )}
                          {cs !== null && <> · score {cs.toFixed(1)}</>}
                        </p>
                      </CardHeader>
                      <CardContent className="space-y-2">
                        {p.catalyst_category && (
                          <p className="text-caption uppercase tracking-[0.14em]">
                            {p.catalyst_category}
                          </p>
                        )}
                        <p className="line-clamp-3 text-small text-muted-foreground">
                          {p.catalyst_description ??
                            "Catalyst detail not yet attached."}
                        </p>
                      </CardContent>
                    </Card>
                  </Link>
                </Reveal>
              )
            })}
          </div>
        )}
      </section>

      {/* ── Portfolios at a glance ─────────────────────────────── */}
      <section aria-labelledby="portfolios-heading" className="space-y-6">
        <Reveal>
          <div className="flex items-baseline justify-between gap-4">
            <h2
              id="portfolios-heading"
              className="text-caption uppercase tracking-[0.18em] text-muted-foreground"
            >
              Portfolio at a glance
            </h2>
            <Link
              href="/dashboard/positions"
              className="inline-flex items-center gap-1 text-caption transition-premium hover:text-foreground"
            >
              Open positions
              <ArrowRight className="size-3" />
            </Link>
          </div>
        </Reveal>
        {portfolioSummaries.length === 0 ? (
          <Reveal>
            <Card size="sm">
              <CardContent className="py-8 text-small text-muted-foreground">
                No portfolios linked to this account yet.
              </CardContent>
            </Card>
          </Reveal>
        ) : (
          <div className="grid gap-5 sm:grid-cols-2">
            {portfolioSummaries.map((p, idx) => (
              <Reveal key={p.id} delay={idx * 80}>
                <Link
                  href="/dashboard/positions"
                  className="group block h-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-xl"
                >
                  <Card
                    size="sm"
                    className="h-full transition-premium duration-300 group-hover:-translate-y-0.5 group-hover:shadow-[var(--shadow-md)]"
                  >
                    <CardHeader>
                      <div className="flex items-baseline justify-between">
                        <CardTitle className="text-[15px]">{p.name}</CardTitle>
                        {p.critical > 0 && (
                          <span className="text-caption uppercase tracking-[0.14em] text-destructive">
                            {p.critical} critical
                          </span>
                        )}
                      </div>
                      <p className="text-caption tabular-nums">
                        {p.holdings}{" "}
                        {p.holdings === 1 ? "holding" : "holdings"}
                      </p>
                    </CardHeader>
                    <CardContent>
                      <p className="text-small text-muted-foreground">
                        {p.alerts === 0 ? (
                          "No active alerts."
                        ) : (
                          <>
                            {p.alerts} alert{p.alerts === 1 ? "" : "s"} in the
                            last week
                            {p.critical > 0 && (
                              <span className="text-destructive">
                                {" — "}
                                {p.critical} critical/high
                              </span>
                            )}
                          </>
                        )}
                      </p>
                    </CardContent>
                  </Card>
                </Link>
              </Reveal>
            ))}
          </div>
        )}
      </section>

      {/* ── Recent activity ─────────────────────────────────────── */}
      <section aria-labelledby="activity-heading" className="space-y-6">
        <Reveal>
          <h2
            id="activity-heading"
            className="text-caption uppercase tracking-[0.18em] text-muted-foreground"
          >
            Recent activity
          </h2>
        </Reveal>
        <Reveal>
          <Card size="sm">
            <CardContent className="py-4">
              {recentEvents.length === 0 ? (
                <p className="text-small text-muted-foreground">
                  No activity yet. Open a brief or research a ticker and it
                  appears here.
                </p>
              ) : (
                <ul className="divide-y divide-border text-small">
                  {recentEvents.map((e, i) => (
                    <li
                      key={i}
                      className="flex items-baseline justify-between gap-4 py-3"
                    >
                      <span className="text-foreground">
                        {prettyEvent(e.event_type)}
                        {typeof e.event_data?.ticker === "string" && (
                          <span className="ml-1.5 font-mono text-caption">
                            {e.event_data.ticker.toUpperCase()}
                          </span>
                        )}
                      </span>
                      <span className="text-caption tabular-nums">
                        {timeAgo(new Date(e.occurred_at))}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </Reveal>
      </section>
    </div>
  )
}
