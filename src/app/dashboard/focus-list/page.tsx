import Link from "next/link"

import { createClient } from "@/lib/supabase/server"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

export const metadata = { title: "Focus list — Fortis" }
export const dynamic = "force-dynamic"

type Row = {
  ticker: string
  rank: number | null
  conviction_grade: "A" | "B" | "C" | "D" | "U" | null
  composite_score: number | null
  conviction_score_adjusted: number | null
  catalyst_category: string | null
  catalyst_description: string | null
  thesis: string | null
  bull_case: string | null
  dossier_complete: boolean | null
  run_date: string
  run_type: string
}

type Metrics = {
  ticker: string
  price: number | null
  day_change_pct: number | null
  relative_volume: number | null
  is_breakout: boolean | null
}

const GRADE_LABEL: Record<string, string> = {
  A: "Grade A — high conviction",
  B: "Grade B — promising",
  C: "Grade C — watch list",
  D: "Grade D — passed on",
  U: "Ungraded — Layer 4 pending",
}

const GRADE_VARIANT = {
  A: "gradeA",
  B: "gradeB",
  C: "gradeC",
  D: "neutral",
  U: "neutral",
} as const

const GRADE_BAR = {
  A: "bg-highlight",
  B: "bg-warning",
  C: "bg-muted-foreground",
  D: "bg-secondary",
  U: "bg-secondary",
} as const

// Catalyst categories worth a chip. 'none' is deliberately absent — the
// LLM's "there is no catalyst" paragraphs never reach the screen; absence
// of a catalyst renders as silence, not filler.
const CATALYST_LABEL: Record<string, string> = {
  earnings: "Earnings",
  analyst_action: "Analyst action",
  technical_event: "Technical event",
  insider_activity: "Insider activity",
  news_event: "News event",
  sec_filing: "SEC filing",
  ma_activity: "M&A",
  guidance: "Guidance",
}

/** True when the catalyst is real (not the 'none' boilerplate). */
function hasRealCatalyst(r: Row): boolean {
  const cat = (r.catalyst_category ?? "none").toLowerCase()
  if (cat === "none" || cat === "") return false
  const d = r.catalyst_description ?? ""
  return !/there is no specific catalyst|MIXED_SIGNALS/i.test(d)
}

/** Strip LLM markdown chrome so excerpts read as set copy. */
function stripMd(s: string): string {
  return s
    .replace(/^\s*#{1,6}\s+/gm, "")     // headings
    .replace(/\*\*([^*]+)\*\*/g, "$1")  // bold
    .replace(/\*([^*]+)\*/g, "$1")      // italics
    .replace(/^[\s*-]+/gm, "")          // list bullets
    .replace(/`+/g, "")
    .replace(/\s+/g, " ")
    .trim()
}

/** The card body: thesis → bull case → derived signal line. */
function cardBody(r: Row, m: Metrics | undefined): { label: string | null; text: string } {
  // Picks whose dossier is still assembling display, but we withhold their
  // unverified prose — grade + metrics only, with an honest note.
  if (!r.dossier_complete) {
    const bits: string[] = []
    if (m?.relative_volume != null && m.relative_volume >= 1.5)
      bits.push(`${m.relative_volume.toFixed(1)}× average volume`)
    if (m?.is_breakout) bits.push("breakout flag")
    return {
      label: "Dossier completing",
      text: `Graded on composite strength${bits.length ? ` — ${bits.join(", ")}` : ""}. The full fact-checked thesis is still being assembled.`,
    }
  }
  if (r.thesis && r.thesis.length > 50) {
    return { label: null, text: stripMd(r.thesis) }
  }
  if (r.bull_case && r.bull_case.length > 50) {
    return { label: "Bull case", text: stripMd(r.bull_case) }
  }
  const bits: string[] = []
  if (m?.relative_volume != null && m.relative_volume >= 1.5)
    bits.push(`${m.relative_volume.toFixed(1)}× average volume`)
  if (m?.is_breakout) bits.push("breakout flag")
  return {
    label: null,
    text: `Advanced on composite strength${bits.length ? ` — ${bits.join(", ")}` : ""}. Open the dossier for the full picture.`,
  }
}

export default async function FocusListPage() {
  const supabase = await createClient()

  // The focus list reflects the LATEST completed market scan. We look up
  // the scan first (canonical source of truth) and then pull its picks.
  const { data: latestScanRow } = await supabase
    .from("market_scans")
    .select("id, scan_timestamp, scan_type, completed_at")
    .eq("status", "complete")
    .order("completed_at", { ascending: false })
    .limit(1)
    .maybeSingle()

  if (!latestScanRow) {
    return (
      <div className="mx-auto max-w-[1280px] px-6 py-14 md:px-10 md:py-16">
        <header className="space-y-2">
          <p className="text-eyebrow">Focus list</p>
          <h1 className="text-h1">No completed scan yet.</h1>
        </header>
        <Card className="mt-12">
          <CardContent className="py-16 text-center text-small text-muted-foreground">
            Picks appear here after the next scan completes (3× per trading
            day — pre-market, midday, post-close).
          </CardContent>
        </Card>
      </div>
    )
  }

  const [{ data: rows }, { data: metricRows }] = await Promise.all([
    supabase
      .from("ranked_focus_list")
      .select(
        "ticker,rank,conviction_grade,composite_score,conviction_score_adjusted,catalyst_category,catalyst_description,thesis,bull_case,dossier_complete,run_date,run_type",
      )
      .eq("scan_id", latestScanRow.id)
      // The whole picked shortlist (20-25) displays; dossier_complete is a
      // label, not a filter. Names still assembling show "dossier completing"
      // and withhold their unverified prose (see cardBody).
      .order("rank", { ascending: true })
      .limit(25),
    supabase
      .from("scan_results")
      .select("ticker,price,day_change_pct,relative_volume,is_breakout")
      .eq("scan_id", latestScanRow.id)
      .eq("advanced", true)
      .limit(80),
  ])

  const picks = (rows ?? []) as Row[]
  const metrics = new Map(
    ((metricRows ?? []) as Metrics[]).map((m) => [m.ticker, m]),
  )

  const grouped: Record<string, Row[]> = {}
  for (const r of picks) {
    const g = r.conviction_grade ?? "U"
    ;(grouped[g] = grouped[g] ?? []).push(r)
  }
  const gradeOrder = ["A", "B", "C", "D", "U"] as const
  const distribution = gradeOrder
    .map((g) => ({ grade: g, count: (grouped[g] ?? []).length }))
    .filter((d) => d.count > 0)

  const lastUpdated =
    latestScanRow.completed_at ?? latestScanRow.scan_timestamp
  const fmtTime = new Date(lastUpdated).toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  })

  return (
    <div className="mx-auto max-w-[1280px] space-y-20 px-6 py-14 md:space-y-24 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-5">
        <div className="space-y-3">
          <p className="text-eyebrow">
            Focus list · {latestScanRow.scan_type} scan · {fmtTime}
          </p>
          <h1 className="text-h1">
            {picks.length} names from the latest scan.
          </h1>
          <p className="text-body-lg max-w-[680px] text-muted-foreground">
            The composite top {picks.length}, grouped by conviction grade.
            Click into any ticker for the full thesis, catalyst, insider
            posture, and bull / bear cases.
          </p>
        </div>

        {/* Grade distribution — the scan's shape in one bar. */}
        {distribution.length > 0 && (
          <div className="max-w-[680px] space-y-2">
            <div className="flex h-1.5 gap-px overflow-hidden rounded-full">
              {distribution.map((d, i) => (
                <span
                  key={d.grade}
                  className={`bar-grow ${GRADE_BAR[d.grade]} h-full`}
                  style={{
                    width: `${(d.count / picks.length) * 100}%`,
                    animationDelay: `${i * 120}ms`,
                  }}
                />
              ))}
            </div>
            <p className="text-caption text-data">
              {distribution
                .map((d) => `${d.count} ${d.grade === "U" ? "ungraded" : d.grade}`)
                .join(" · ")}
            </p>
          </div>
        )}
      </Reveal>

      {gradeOrder.map((grade) => {
        const list = grouped[grade] ?? []
        if (list.length === 0) return null
        return (
          <section key={grade} className="space-y-6">
            <Reveal>
              <div className="flex items-baseline justify-between gap-4">
                <h2 className="text-eyebrow">{GRADE_LABEL[grade]}</h2>
                <span className="text-caption text-data">
                  {list.length} {list.length === 1 ? "pick" : "picks"}
                </span>
              </div>
            </Reveal>
            <div className="grid gap-5 md:grid-cols-2">
              {list.slice(0, 24).map((r, idx) => {
                const m = metrics.get(r.ticker)
                const body = cardBody(r, m)
                const catalyst = hasRealCatalyst(r)
                  ? CATALYST_LABEL[(r.catalyst_category ?? "").toLowerCase()] ??
                    (r.catalyst_category ?? "").replace(/_/g, " ")
                  : null
                const dchg = m?.day_change_pct != null ? Number(m.day_change_pct) : null
                return (
                  <Reveal key={`${grade}-${r.ticker}`} delay={Math.min(idx, 6) * 60}>
                    <Link
                      href={`/dashboard/research/${r.ticker}`}
                      className="group block h-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                    >
                      <Card
                        size="sm"
                        className="h-full transition-premium duration-300 group-hover:-translate-y-0.5 group-hover:shadow-[var(--shadow-md)]"
                      >
                        <CardHeader>
                          <div className="flex items-center justify-between gap-3">
                            <CardTitle className="flex items-center gap-2.5">
                              <span className="text-data text-[20px] font-semibold">
                                {r.ticker}
                              </span>
                              <Badge variant={GRADE_VARIANT[grade]}>
                                {grade === "U" ? "Pending" : grade}
                              </Badge>
                              {catalyst && (
                                <Badge variant="accent">{catalyst}</Badge>
                              )}
                              {!r.dossier_complete && (
                                <Badge variant="neutral">Dossier completing</Badge>
                              )}
                            </CardTitle>
                          </div>
                          <p className="text-caption text-data">
                            #{r.rank ?? "—"} · score{" "}
                            {r.composite_score != null
                              ? Number(r.composite_score).toFixed(1)
                              : "—"}
                            {m?.price != null && (
                              <>
                                {" · "}
                                {Number(m.price).toLocaleString("en-US", {
                                  minimumFractionDigits: 2,
                                  maximumFractionDigits: 2,
                                })}
                              </>
                            )}
                            {dchg != null && (
                              <>
                                {" "}
                                <span className={dchg >= 0 ? "text-gain" : "text-loss"}>
                                  {dchg >= 0 ? "+" : ""}
                                  {dchg.toFixed(2)}%
                                </span>
                              </>
                            )}
                          </p>
                        </CardHeader>
                        <CardContent>
                          {body.label && (
                            <p className="text-eyebrow mb-1.5">{body.label}</p>
                          )}
                          <p className="line-clamp-3 text-small text-muted-foreground">
                            {body.text}
                          </p>
                        </CardContent>
                      </Card>
                    </Link>
                  </Reveal>
                )
              })}
            </div>
          </section>
        )
      })}
    </div>
  )
}
