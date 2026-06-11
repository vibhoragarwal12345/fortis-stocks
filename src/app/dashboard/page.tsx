import Link from "next/link"
import { ArrowRight } from "lucide-react"

import { createClient } from "@/lib/supabase/server"
import { trackEvent } from "@/lib/track"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

export const metadata = { title: "Today — Fortis" }
export const dynamic = "force-dynamic"

type ScanRow = {
  id: number
  scan_timestamp: string
  scan_type: "premarket" | "intraday" | "postclose" | "manual"
  status: "running" | "complete" | "failed"
  tickers_scanned_count: number | null
  shortlist_count: number | null
  graded_count: number | null
  completed_at: string | null
}

type ScanResult = {
  ticker: string
  price: number | null
  day_change_pct: number | null
  gap_pct: number | null
  return_5d_pct: number | null
  return_20d_pct: number | null
  relative_volume: number | null
  rsi_14: number | null
  pct_from_52w_high: number | null
  pct_from_52w_low: number | null
  is_breakout: boolean | null
  is_breakdown: boolean | null
  composite_score: number | null
  rank: number | null
  data_as_of: string | null
}

function num(v: unknown): number | null {
  if (v == null) return null
  const n = typeof v === "number" ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

function pct(v: number | null, digits = 2): string {
  if (v == null) return "—"
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(digits)}%`
}

function timeAgo(iso: string | null | undefined, now: number): string {
  if (!iso) return "—"
  const t = new Date(iso).getTime()
  const m = Math.round((now - t) / 60000)
  if (m < 1) return "just now"
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.round(h / 24)
  return `${d}d ago`
}

function fmtTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return d.toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  })
}

export default async function DashboardHomePage() {
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) return null

  await trackEvent("dashboard_today_opened")

  // ── Latest completed scan ────────────────────────────────────────────
  const { data: latestScanRow } = await supabase
    .from("market_scans")
    .select(
      "id, scan_timestamp, scan_type, status, tickers_scanned_count, shortlist_count, graded_count, completed_at",
    )
    .eq("status", "complete")
    .order("completed_at", { ascending: false })
    .limit(1)
    .maybeSingle()
  const scan = latestScanRow as ScanRow | null

  // ── Shortlist for that scan ──────────────────────────────────────────
  let shortlist: ScanResult[] = []
  if (scan) {
    const { data } = await supabase
      .from("scan_results")
      .select(
        "ticker,price,day_change_pct,gap_pct,return_5d_pct,return_20d_pct,relative_volume,rsi_14,pct_from_52w_high,pct_from_52w_low,is_breakout,is_breakdown,composite_score,rank,data_as_of",
      )
      .eq("scan_id", scan.id)
      .eq("advanced", true)
      .order("rank", { ascending: true })
      .limit(40)
    shortlist = (data ?? []) as ScanResult[]
  }

  const nowMs = Date.now()
  const lastUpdated = scan?.completed_at ?? scan?.scan_timestamp ?? null

  return (
    <div className="mx-auto max-w-[1280px] space-y-14 px-6 py-12 md:space-y-16 md:px-10 md:py-14">
      {/* ── Last-updated banner (PART 6 honest labelling) ──────────── */}
      <Reveal as="header">
        <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
          <div className="space-y-2">
            <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
              Market scan · {scan?.scan_type ?? "—"}
            </p>
            <h1 className="text-h1">Top opportunities, right now.</h1>
          </div>
          <div className="flex flex-col items-end gap-3">
            <DataFreshness lastUpdated={lastUpdated} nowMs={nowMs} scan={scan} />
          </div>
        </div>
      </Reveal>

      {!scan && (
        <Reveal>
          <Card>
            <CardContent className="py-16 text-center text-small text-muted-foreground">
              No completed scan yet. The first run lands on the next cron
              tick (3× per trading day — pre-market, midday, post-close).
            </CardContent>
          </Card>
        </Reveal>
      )}

      {scan && shortlist.length === 0 && (
        <Reveal>
          <Card>
            <CardContent className="py-16 text-center text-small text-muted-foreground">
              Scan completed but the shortlist is empty. The composite
              scorer found no names above threshold this cycle.
            </CardContent>
          </Card>
        </Reveal>
      )}

      {/* ── Shortlist ───────────────────────────────────────────────── */}
      {scan && shortlist.length > 0 && (
        <section className="space-y-6">
          <Reveal>
            <div className="flex items-baseline justify-between gap-4">
              <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
                Shortlist · top {shortlist.length} by composite score
              </h2>
              <p className="text-caption tabular-nums">
                {scan.tickers_scanned_count ?? 0} tickers scanned
              </p>
            </div>
          </Reveal>

          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {shortlist.map((r, idx) => (
              <Reveal key={r.ticker} delay={Math.min(idx, 8) * 50}>
                <Link
                  href={`/dashboard/research/${r.ticker}`}
                  className="group block h-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-xl"
                >
                  <ScanCard r={r} />
                </Link>
              </Reveal>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────
//  Components
// ─────────────────────────────────────────────────────────────────────────

function DataFreshness({
  lastUpdated,
  nowMs,
  scan,
}: {
  lastUpdated: string | null
  nowMs: number
  scan: ScanRow | null
}) {
  return (
    <div className="rounded-md border border-border bg-card px-4 py-3 text-small">
      <p className="font-medium text-foreground">
        Last updated {timeAgo(lastUpdated, nowMs)}
      </p>
      <p className="text-caption tabular-nums">
        {fmtTimestamp(lastUpdated)}{" · "}
        <span className="text-muted-foreground">15-min delayed price data</span>
      </p>
      {scan && (
        <p className="mt-1 text-caption text-muted-foreground tabular-nums">
          {scan.tickers_scanned_count ?? 0} scanned ·{" "}
          {scan.shortlist_count ?? 0} shortlisted
          {scan.graded_count != null && scan.graded_count > 0 && (
            <> · {scan.graded_count} graded</>
          )}
        </p>
      )}
    </div>
  )
}

function ScanCard({ r }: { r: ScanResult }) {
  const score = num(r.composite_score) ?? 0
  const price = num(r.price)
  const dchg = num(r.day_change_pct)
  const gap = num(r.gap_pct)
  const ret20 = num(r.return_20d_pct)
  const relVol = num(r.relative_volume)
  const rsi = num(r.rsi_14)
  const fromHigh = num(r.pct_from_52w_high)

  return (
    <Card
      size="sm"
      className="h-full transition-premium duration-300 group-hover:-translate-y-0.5 group-hover:shadow-[var(--shadow-md)]"
    >
      <CardHeader>
        <div className="flex items-baseline justify-between">
          <CardTitle className="flex items-baseline gap-2">
            <span className="font-mono text-[20px] tracking-tight">
              {r.ticker}
            </span>
            <span className="text-caption tabular-nums text-muted-foreground">
              #{r.rank ?? "—"}
            </span>
          </CardTitle>
          <span className="text-caption uppercase tracking-[0.14em] text-foreground tabular-nums">
            {score.toFixed(0)}
          </span>
        </div>
        <p className="text-caption tabular-nums">
          {price != null ? `$${price.toFixed(2)}` : "—"}
          {dchg != null && (
            <>
              {" · "}
              <span
                className={dchg >= 0 ? "text-foreground" : "text-destructive"}
              >
                {pct(dchg)}
              </span>
            </>
          )}
          {gap != null && Math.abs(gap) >= 0.5 && (
            <>
              {" · gap "}
              <span
                className={gap >= 0 ? "text-foreground" : "text-destructive"}
              >
                {pct(gap)}
              </span>
            </>
          )}
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-caption">
          <Metric
            label="20d ret"
            value={pct(ret20)}
            good={ret20 != null && ret20 > 0}
          />
          <Metric
            label="rel vol"
            value={relVol != null ? `${relVol.toFixed(2)}×` : "—"}
            good={relVol != null && relVol > 1.2}
          />
          <Metric label="RSI" value={rsi != null ? rsi.toFixed(0) : "—"} />
          <Metric label="52w hi" value={pct(fromHigh)} />
          <div className="col-span-2">
            <BadgeRow r={r} />
          </div>
        </dl>
        <p className="flex items-center gap-1 text-caption transition-premium group-hover:text-foreground">
          Open research <ArrowRight className="size-3" />
        </p>
      </CardContent>
    </Card>
  )
}

function Metric({
  label,
  value,
  good,
}: {
  label: string
  value: string
  good?: boolean
}) {
  return (
    <div className="space-y-0.5">
      <dt className="text-muted-foreground uppercase tracking-[0.10em]">
        {label}
      </dt>
      <dd className={`tabular-nums ${good ? "text-foreground" : ""}`}>
        {value}
      </dd>
    </div>
  )
}

function BadgeRow({ r }: { r: ScanResult }) {
  const flags: string[] = []
  if (r.is_breakout) flags.push("breakout")
  if (r.is_breakdown) flags.push("breakdown")
  if (r.relative_volume != null && r.relative_volume >= 2) flags.push("vol surge")
  if (r.rsi_14 != null && r.rsi_14 <= 30) flags.push("oversold")
  if (r.rsi_14 != null && r.rsi_14 >= 70) flags.push("overbought")
  if (flags.length === 0) return null
  return (
    <ul className="flex flex-wrap gap-1.5">
      {flags.map((f) => (
        <li
          key={f}
          className="rounded border border-border px-2 py-0.5 text-[10px] uppercase tracking-[0.12em] text-foreground/80"
        >
          {f}
        </li>
      ))}
    </ul>
  )
}
