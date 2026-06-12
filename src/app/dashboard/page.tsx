import Link from "next/link"

import { createClient } from "@/lib/supabase/server"
import { trackEvent } from "@/lib/track"
import { Card, CardContent } from "@/components/ui/card"
import { CountUp } from "@/components/ui/count-up"
import { Reveal } from "@/components/ui/reveal"
import { WordReveal } from "@/components/ui/word-reveal"
import { ShortlistTable, type ShortlistRow } from "@/components/shortlist-table"

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
  return_52w_pct: number | null
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
        "ticker,price,day_change_pct,gap_pct,return_5d_pct,return_20d_pct,relative_volume,rsi_14,return_52w_pct,is_breakout,is_breakdown,composite_score,rank,data_as_of",
      )
      .eq("scan_id", scan.id)
      .eq("advanced", true)
      .order("rank", { ascending: true })
      .limit(40)
    shortlist = (data ?? []) as ScanResult[]
  }

  // Server component: rendered once per request, so "now" is stable here.
  // eslint-disable-next-line react-hooks/purity
  const nowMs = Date.now()
  const lastUpdated = scan?.completed_at ?? scan?.scan_timestamp ?? null

  return (
    <div className="mx-auto max-w-[1280px] space-y-14 px-6 py-12 md:space-y-16 md:px-10 md:py-14">
      {/* ── Last-updated banner (PART 6 honest labelling) ──────────── */}
      <Reveal as="header">
        <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
          <div className="space-y-2">
            <p className="text-eyebrow">
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

      {/* ── The Wire — what this scan actually said ─────────────────── */}
      {scan && shortlist.length > 0 && (
        <ScanWire rows={shortlist.map(toShortlistRow)} />
      )}

      {/* ── Shortlist — the terminal core ───────────────────────────── */}
      {scan && shortlist.length > 0 && (
        <section className="space-y-6">
          <Reveal>
            <div className="flex items-baseline justify-between gap-4">
              <h2 className="text-eyebrow">
                Shortlist · top {shortlist.length} by composite score
              </h2>
              <p className="text-caption text-data">
                {scan.tickers_scanned_count ?? 0} tickers scanned
              </p>
            </div>
          </Reveal>

          <Reveal delay={80}>
            <ShortlistTable rows={shortlist.map(toShortlistRow)} />
          </Reveal>
        </section>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────
//  The Wire — headlines derived from the scan itself. No LLM, no hype:
//  every number on screen is a row in scan_results.
// ─────────────────────────────────────────────────────────────────────────

function ScanWire({ rows }: { rows: ShortlistRow[] }) {
  const withChange = rows.filter((r) => r.dayChangePct != null)
  if (withChange.length === 0) return null

  const topMover = [...withChange].sort(
    (a, b) => Math.abs(b.dayChangePct!) - Math.abs(a.dayChangePct!),
  )[0]
  const volLeader = [...rows]
    .filter((r) => r.relVol != null)
    .sort((a, b) => b.relVol! - a.relVol!)[0]
  const breakouts = rows.filter((r) => r.breakout).length
  const topScore = [...rows]
    .filter((r) => r.score != null)
    .sort((a, b) => b.score! - a.score!)[0]

  const moverPct = topMover.dayChangePct!
  const lead =
    Math.abs(moverPct) >= 1
      ? `${topMover.ticker} leads the tape — ${moverPct >= 0 ? "up" : "down"} ${Math.abs(moverPct).toFixed(1)}% ${
          topMover.relVol != null && topMover.relVol >= 1.5
            ? `on ${topMover.relVol.toFixed(1)}× volume`
            : "this session"
        }.`
      : `A quiet tape — ${topMover.ticker} is the biggest mover at ${moverPct >= 0 ? "+" : ""}${moverPct.toFixed(2)}%.`

  const cards = [
    topMover && {
      href: `/dashboard/research/${topMover.ticker}`,
      label: `Top mover · ${topMover.ticker}`,
      value: (
        <CountUp
          value={moverPct}
          signed
          decimals={2}
          suffix="%"
          className={moverPct >= 0 ? "text-gain" : "text-loss"}
        />
      ),
    },
    volLeader &&
      volLeader.relVol != null && {
        href: `/dashboard/research/${volLeader.ticker}`,
        label: `Volume leader · ${volLeader.ticker}`,
        value: (
          <CountUp
            value={volLeader.relVol}
            decimals={2}
            suffix="× avg"
            className="text-highlight"
          />
        ),
      },
    {
      href: null,
      label: "Breakouts flagged",
      value: <CountUp value={breakouts} className={breakouts > 0 ? "text-gain" : undefined} />,
    },
    topScore && {
      href: `/dashboard/research/${topScore.ticker}`,
      label: `Top composite · ${topScore.ticker}`,
      value: <CountUp value={topScore.score!} decimals={1} />,
    },
  ].filter(Boolean) as {
    href: string | null
    label: string
    value: React.ReactNode
  }[]

  return (
    <section className="space-y-6">
      <Reveal>
        <h2 className="text-eyebrow flex items-center gap-2.5">
          <span aria-hidden className="relative flex size-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-highlight opacity-60" />
            <span className="relative inline-flex size-1.5 rounded-full bg-highlight" />
          </span>
          The wire · derived from this scan
        </h2>
      </Reveal>
      <WordReveal as="p" className="text-h2 max-w-[760px]" text={lead} step={50} />
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border bg-border lg:grid-cols-4">
        {cards.map((c, i) => {
          const inner = (
            <>
              <p className="text-data text-h3">{c.value}</p>
              <p className="mt-1 text-caption">{c.label}</p>
            </>
          )
          return c.href ? (
            <Link
              key={c.label}
              href={c.href}
              className="row-in block bg-card px-5 py-4 transition-premium hover:bg-secondary/60"
              style={{ animationDelay: `${i * 70}ms` }}
            >
              {inner}
            </Link>
          ) : (
            <div
              key={c.label}
              className="row-in bg-card px-5 py-4"
              style={{ animationDelay: `${i * 70}ms` }}
            >
              {inner}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function toShortlistRow(r: ScanResult): ShortlistRow {
  return {
    ticker: r.ticker,
    rank: num(r.rank),
    score: num(r.composite_score),
    price: num(r.price),
    dayChangePct: num(r.day_change_pct),
    gapPct: num(r.gap_pct),
    ret20Pct: num(r.return_20d_pct),
    relVol: num(r.relative_volume),
    rsi: num(r.rsi_14),
    ret52wPct: num(r.return_52w_pct),
    breakout: !!r.is_breakout,
    breakdown: !!r.is_breakdown,
  }
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
  // Fresh = younger than the 2h manual-refresh window.
  const fresh =
    lastUpdated != null &&
    nowMs - new Date(lastUpdated).getTime() < 2 * 60 * 60 * 1000

  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3 text-small shadow-[var(--shadow-card)]">
      <p className="flex items-center gap-2 font-medium text-foreground">
        <span aria-hidden className="relative flex size-1.5">
          {fresh && (
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-highlight opacity-60" />
          )}
          <span
            className={`relative inline-flex size-1.5 rounded-full ${
              fresh ? "bg-highlight" : "bg-muted-foreground"
            }`}
          />
        </span>
        Last updated {timeAgo(lastUpdated, nowMs)}
      </p>
      <p className="text-caption text-data">
        {fmtTimestamp(lastUpdated)}{" · "}
        <span className="text-muted-foreground">15-min delayed price data</span>
      </p>
      {scan && (
        <p className="mt-1 text-caption text-data text-muted-foreground">
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
