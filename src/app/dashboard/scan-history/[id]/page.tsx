import Link from "next/link"
import { notFound } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { isOwner } from "@/lib/permissions"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

export const metadata = { title: "Scan — Fortis" }
export const dynamic = "force-dynamic"

type Scan = {
  id: number
  scan_timestamp: string
  scan_type: string
  status: string
  tickers_scanned_count: number | null
  shortlist_count: number | null
  graded_count: number | null
  completed_at: string | null
  notes: string | null
}

type ScanResult = {
  ticker: string
  price: number | null
  day_change_pct: number | null
  gap_pct: number | null
  return_20d_pct: number | null
  relative_volume: number | null
  rsi_14: number | null
  is_breakout: boolean | null
  is_breakdown: boolean | null
  composite_score: number | null
  rank: number | null
}

function pct(v: number | null): string {
  if (v == null) return "—"
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(2)}%`
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  })
}

export default async function ScanDetailPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id: idStr } = await params
  const id = parseInt(idStr, 10)
  if (Number.isNaN(id)) notFound()

  const supabase = await createClient()

  // Owner-only operational surface (same gate as the scan-history list).
  // Anyone else -> 404, so a guessed scan-id URL reveals nothing.
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!isOwner(user)) notFound()

  const { data: scanRow } = await supabase
    .from("market_scans")
    .select(
      "id, scan_timestamp, scan_type, status, tickers_scanned_count, shortlist_count, graded_count, completed_at, notes",
    )
    .eq("id", id)
    .maybeSingle()

  const scan = scanRow as Scan | null
  if (!scan) notFound()

  const { data: rows } = await supabase
    .from("scan_results")
    .select(
      "ticker,price,day_change_pct,gap_pct,return_20d_pct,relative_volume,rsi_14,is_breakout,is_breakdown,composite_score,rank",
    )
    .eq("scan_id", id)
    .eq("advanced", true)
    .order("rank", { ascending: true })

  const shortlist = (rows ?? []) as ScanResult[]

  return (
    <div className="mx-auto max-w-[1280px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <Link
          href="/dashboard/scan-history"
          className="inline-flex items-center gap-1 text-caption transition-premium hover:text-foreground"
        >
          ← Scan history
        </Link>
        <p className="text-eyebrow">
          Scan #{scan.id} · {scan.scan_type}
        </p>
        <h1 className="text-h1">{fmtTime(scan.scan_timestamp)}</h1>
        <p className="text-small text-muted-foreground tabular-nums">
          {scan.tickers_scanned_count ?? 0} scanned ·{" "}
          {scan.shortlist_count ?? 0} shortlisted
          {scan.graded_count != null && scan.graded_count > 0 && (
            <> · {scan.graded_count} graded</>
          )}
          {scan.completed_at && (
            <> · completed {fmtTime(scan.completed_at)}</>
          )}
        </p>
        {scan.notes && (
          <p className="text-caption text-destructive">{scan.notes}</p>
        )}
      </Reveal>

      {shortlist.length === 0 ? (
        <Reveal>
          <Card>
            <CardContent className="py-16 text-center text-small text-muted-foreground">
              No shortlist rows for this scan. It may have failed before
              Layer 2 completed.
            </CardContent>
          </Card>
        </Reveal>
      ) : (
        <section className="space-y-6">
          <Reveal>
            <h2 className="text-eyebrow">
              Shortlist · {shortlist.length} names
            </h2>
          </Reveal>
          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {shortlist.map((r, idx) => (
              <Reveal key={r.ticker} delay={Math.min(idx, 8) * 50}>
                <Link
                  href={`/dashboard/research/${r.ticker}`}
                  className="group block h-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                >
                  <Card
                    size="sm"
                    className="h-full transition-premium duration-300 group-hover:-translate-y-0.5 group-hover:shadow-[var(--shadow-md)]"
                  >
                    <CardHeader>
                      <div className="flex items-baseline justify-between">
                        <CardTitle className="font-mono text-[20px] tracking-tight">
                          {r.ticker}
                        </CardTitle>
                        <span className="text-caption uppercase tracking-[0.14em] text-foreground tabular-nums">
                          {r.composite_score?.toFixed(0) ?? "—"}
                        </span>
                      </div>
                      <p className="text-caption tabular-nums">
                        #{r.rank ?? "—"} ·{" "}
                        {r.price != null ? `$${r.price.toFixed(2)}` : "—"} ·{" "}
                        {pct(r.day_change_pct)}
                      </p>
                    </CardHeader>
                    <CardContent>
                      <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-caption">
                        <div className="space-y-0.5">
                          <dt className="text-muted-foreground uppercase tracking-[0.10em]">
                            20d ret
                          </dt>
                          <dd className="tabular-nums">{pct(r.return_20d_pct)}</dd>
                        </div>
                        <div className="space-y-0.5">
                          <dt className="text-muted-foreground uppercase tracking-[0.10em]">
                            rel vol
                          </dt>
                          <dd className="tabular-nums">
                            {r.relative_volume != null
                              ? `${r.relative_volume.toFixed(2)}×`
                              : "—"}
                          </dd>
                        </div>
                        <div className="space-y-0.5">
                          <dt className="text-muted-foreground uppercase tracking-[0.10em]">
                            RSI
                          </dt>
                          <dd className="tabular-nums">
                            {r.rsi_14 != null ? r.rsi_14.toFixed(0) : "—"}
                          </dd>
                        </div>
                      </dl>
                    </CardContent>
                  </Card>
                </Link>
              </Reveal>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
