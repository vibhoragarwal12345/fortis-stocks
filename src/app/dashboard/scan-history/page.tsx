import Link from "next/link"
import { notFound } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { isOwner } from "@/lib/permissions"
import { Card, CardContent } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

export const metadata = { title: "Scan history · Christopher Edwards Financial Associates" }
export const dynamic = "force-dynamic"

type Scan = {
  id: number
  scan_timestamp: string
  scan_type: "premarket" | "intraday" | "postclose" | "manual"
  status: "running" | "complete" | "failed"
  tickers_scanned_count: number | null
  shortlist_count: number | null
  graded_count: number | null
  completed_at: string | null
  notes: string | null
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

function durationSeconds(scan: Scan): number | null {
  if (!scan.completed_at) return null
  return Math.round(
    (new Date(scan.completed_at).getTime() -
      new Date(scan.scan_timestamp).getTime()) /
      1000,
  )
}

const STATUS_TONE: Record<string, string> = {
  complete: "text-foreground",
  running: "text-muted-foreground",
  failed: "text-destructive",
}

export default async function ScanHistoryPage() {
  const supabase = await createClient()

  // Owner-only operational surface. For anyone else (incl. signed-in client
  // tenants) the page must not exist -- 404 so the route isn't even revealed.
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!isOwner(user)) notFound()

  const { data } = await supabase
    .from("market_scans")
    .select(
      "id, scan_timestamp, scan_type, status, tickers_scanned_count, shortlist_count, graded_count, completed_at, notes",
    )
    .order("scan_timestamp", { ascending: false })
    .limit(50)

  const scans = (data ?? []) as Scan[]

  return (
    <div className="mx-auto max-w-[1280px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <p className="text-eyebrow">
          Scan history
        </p>
        <h1 className="text-h1">The last {scans.length} scans.</h1>
        <p className="text-body-lg max-w-[680px] text-muted-foreground">
          Every 2-hourly scan in reverse chronological order. Click into a
          completed scan to see the shortlist it produced.
        </p>
      </Reveal>

      {scans.length === 0 ? (
        <Reveal>
          <Card>
            <CardContent className="py-16 text-center text-small text-muted-foreground">
              No scans yet. The first one lands on the next cron tick.
            </CardContent>
          </Card>
        </Reveal>
      ) : (
        <Reveal>
          <div className="overflow-x-auto">
            <table className="w-full text-small">
              <thead>
                <tr className="text-left">
                  <TH>Scan</TH>
                  <TH>Type</TH>
                  <TH>Status</TH>
                  <TH>Scanned</TH>
                  <TH>Shortlist</TH>
                  <TH>Graded</TH>
                  <TH>Duration</TH>
                  <TH>{""}</TH>
                </tr>
              </thead>
              <tbody>
                {scans.map((s) => {
                  const dur = durationSeconds(s)
                  return (
                    <tr
                      key={s.id}
                      className="border-t border-border transition-premium hover:bg-secondary/40"
                    >
                      <TD>
                        <span className="text-muted-foreground tabular-nums">
                          #{s.id}
                        </span>
                        <span className="ml-3 text-foreground tabular-nums">
                          {fmtTime(s.scan_timestamp)}
                        </span>
                      </TD>
                      <TD className="uppercase tracking-[0.12em] text-caption">
                        {s.scan_type}
                      </TD>
                      <TD>
                        <span
                          className={`text-caption uppercase tracking-[0.14em] ${
                            STATUS_TONE[s.status] ?? "text-muted-foreground"
                          }`}
                        >
                          {s.status}
                        </span>
                      </TD>
                      <TD className="tabular-nums">
                        {s.tickers_scanned_count ?? "—"}
                      </TD>
                      <TD className="tabular-nums">
                        {s.shortlist_count ?? "—"}
                      </TD>
                      <TD className="tabular-nums">
                        {s.graded_count != null && s.graded_count > 0
                          ? s.graded_count
                          : "—"}
                      </TD>
                      <TD className="tabular-nums text-muted-foreground">
                        {dur != null ? `${dur}s` : "—"}
                      </TD>
                      <TD className="text-right">
                        {s.status === "complete" ? (
                          <Link
                            href={`/dashboard/scan-history/${s.id}`}
                            className="text-caption uppercase tracking-[0.14em] transition-premium hover:text-foreground"
                          >
                            View →
                          </Link>
                        ) : (
                          <span className="text-caption text-muted-foreground">
                            —
                          </span>
                        )}
                      </TD>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Reveal>
      )}
    </div>
  )
}

function TH({ children }: { children: React.ReactNode }) {
  return (
    <th className="py-3 pr-6 text-caption uppercase tracking-[0.14em] font-medium text-muted-foreground">
      {children}
    </th>
  )
}

function TD({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return <td className={`py-3 pr-6 ${className ?? ""}`}>{children}</td>
}
