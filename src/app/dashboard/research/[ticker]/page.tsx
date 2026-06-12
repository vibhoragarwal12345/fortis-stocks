import Link from "next/link"

import { createClient } from "@/lib/supabase/server"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

const GRADE_VARIANT = { A: "gradeA", B: "gradeB", C: "gradeC", D: "neutral" } as const

export const metadata = { title: "Research — Fortis" }
export const dynamic = "force-dynamic"

type Pick = {
  ticker: string
  run_date: string
  run_type: string
  conviction_grade: "A" | "B" | "C" | "D" | null
  composite_score: number | null
  conviction_score_adjusted: number | null
  thesis: string | null
  catalyst_category: string | null
  catalyst_description: string | null
  bull_case: string | null
  bear_case: string | null
  price_target_upside: number | null
  price_target_downside: number | null
  position_size_guidance: string | null
}

export default async function ResearchPage({
  params,
}: {
  params: Promise<{ ticker: string }>
}) {
  const { ticker: rawTicker } = await params
  const ticker = rawTicker.toUpperCase()
  const supabase = await createClient()

  // Latest deep-analysis row (from a recent scan that ran Layer 3+4 on
  // this ticker). Tickers outside the latest shortlist will be null.
  const { data: latestPickRow } = await supabase
    .from("ranked_focus_list")
    .select(
      "ticker,run_date,run_type,conviction_grade,composite_score,conviction_score_adjusted,thesis,catalyst_category,catalyst_description,bull_case,bear_case,price_target_upside,price_target_downside,position_size_guidance",
    )
    .eq("ticker", ticker)
    .order("run_date", { ascending: false })
    .order("run_type", { ascending: false })
    .limit(1)
    .maybeSingle()

  const pick = latestPickRow as Pick | null

  // Latest Layer-1 scan metrics for the ticker -- this is what gives the
  // page real-time market context even when the ticker is NOT on the
  // current shortlist.
  const { data: latestScanRow } = await supabase
    .from("scan_results")
    .select(
      "scan_id,price,day_change_pct,gap_pct,return_5d_pct,return_20d_pct,relative_volume,rsi_14,return_52w_pct,is_breakout,is_breakdown,composite_score,rank,data_as_of",
    )
    .eq("ticker", ticker)
    .order("scan_id", { ascending: false })
    .limit(1)
    .maybeSingle()

  type ScanMetrics = {
    scan_id: number
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
  const scan = latestScanRow as ScanMetrics | null

  const { data: form4 } = await supabase
    .from("form4_transactions")
    .select(
      "transaction_code,is_directional_signal,filing_date,person_name,person_title",
    )
    .eq("ticker", ticker)
    // Server component: per-request "now" for the 90-day filing window.
    // eslint-disable-next-line react-hooks/purity
    .gte("filing_date", new Date(Date.now() - 90 * 86_400_000).toISOString())
    .order("filing_date", { ascending: false })
    .limit(50)

  const insider = (form4 ?? []) as Array<{
    transaction_code: string | null
    is_directional_signal: boolean
    filing_date: string
    person_name: string | null
    person_title: string | null
  }>
  const buys = insider.filter(
    (i) =>
      i.is_directional_signal &&
      (i.transaction_code || "").toUpperCase() === "P",
  ).length
  const sells = insider.filter(
    (i) =>
      i.is_directional_signal &&
      ["S", "V"].includes((i.transaction_code || "").toUpperCase()),
  ).length

  if (!pick && !scan) {
    return (
      <div className="mx-auto max-w-[1120px] px-6 py-14 md:px-10 md:py-16 space-y-12">
        <BackLink />
        <header className="space-y-3">
          <p className="text-eyebrow">Research</p>
          <h1 className="text-data text-h1">{ticker}</h1>
        </header>
        <Card>
          <CardContent className="py-16 text-center text-small text-muted-foreground">
            <code className="font-mono">{ticker}</code> isn&rsquo;t in the
            current liquid universe. The universe refreshes weekly with
            names that have $1M+ average daily dollar volume; if this
            ticker meets that bar it&rsquo;ll appear after the next refresh.
          </CardContent>
        </Card>
      </div>
    )
  }

  // The page header shows either the scan or the pick metadata --
  // whichever is fresher. If we only have scan metrics (Layer 1+2), we
  // skip the deep-analysis sections gracefully.
  const headerRunDate = pick?.run_date ?? "—"
  const headerRunType = pick?.run_type ?? "scan"

  return (
    <div className="mx-auto max-w-[1120px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <BackLink />
        <p className="text-eyebrow">
          Research · {headerRunDate} {headerRunType}
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <h1 className="text-data text-display">{ticker}</h1>
          {pick?.conviction_grade && (
            <Badge
              variant={GRADE_VARIANT[pick.conviction_grade]}
              className="translate-y-1 text-[12px]"
            >
              Grade {pick.conviction_grade}
            </Badge>
          )}
        </div>
        <p className="text-small text-muted-foreground">
          Composite score{" "}
          <span className="text-data text-foreground">
            {pick?.composite_score != null
              ? Number(pick.composite_score).toFixed(1)
              : "—"}
          </span>
          {pick?.conviction_score_adjusted != null && (
            <>
              {" · conviction-adjusted "}
              <span className="text-data text-foreground">
                {Number(pick.conviction_score_adjusted).toFixed(1)}
              </span>
            </>
          )}
        </p>
      </Reveal>

      {scan && (
        <Reveal as="section" className="space-y-5">
          <h2 className="text-eyebrow">Latest market scan · 15-min delayed</h2>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-5 sm:grid-cols-4">
            <Stat
              label="Price"
              value={scan.price != null ? `$${scan.price.toFixed(2)}` : "—"}
            />
            <Stat
              label="Day change"
              value={fmtPct(scan.day_change_pct)}
              tone={scan.day_change_pct ?? 0}
            />
            <Stat
              label="20d return"
              value={fmtPct(scan.return_20d_pct)}
              tone={scan.return_20d_pct ?? 0}
            />
            <Stat
              label="Rel. volume"
              value={
                scan.relative_volume != null
                  ? `${scan.relative_volume.toFixed(2)}×`
                  : "—"
              }
            />
            <Stat
              label="RSI(14)"
              value={scan.rsi_14 != null ? scan.rsi_14.toFixed(0) : "—"}
            />
            <Stat
              label="52w change"
              value={fmtPct(scan.return_52w_pct)}
              tone={scan.return_52w_pct ?? 0}
            />
            <Stat
              label="Composite"
              value={
                scan.composite_score != null
                  ? scan.composite_score.toFixed(0)
                  : "—"
              }
            />
          </dl>
        </Reveal>
      )}

      {pick?.thesis && (
        <Reveal as="section" className="space-y-5">
          <h2 className="text-eyebrow">Thesis</h2>
          <p className="text-body-lg whitespace-pre-line text-foreground/90">
            {pick.thesis}
          </p>
        </Reveal>
      )}

      <section className="grid gap-5 md:grid-cols-2">
        {pick?.bull_case && (
          <Reveal>
            <Card size="sm" className="h-full">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-[15px]">
                  <span aria-hidden className="size-1.5 rounded-full bg-gain" />
                  Bull case
                </CardTitle>
              </CardHeader>
              <CardContent className="text-small text-foreground/85 leading-relaxed">
                {pick.bull_case}
              </CardContent>
            </Card>
          </Reveal>
        )}
        {pick?.bear_case && (
          <Reveal delay={80}>
            <Card size="sm" className="h-full">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-[15px]">
                  <span aria-hidden className="size-1.5 rounded-full bg-loss" />
                  Bear case
                </CardTitle>
              </CardHeader>
              <CardContent className="text-small text-foreground/85 leading-relaxed">
                {pick.bear_case}
              </CardContent>
            </Card>
          </Reveal>
        )}
      </section>

      {(pick?.price_target_upside != null ||
        pick?.price_target_downside != null) && (
        <Reveal as="section" className="space-y-5">
          <h2 className="text-eyebrow">Price targets · debate synthesis</h2>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-5 sm:grid-cols-4">
            <Stat
              label="Upside target"
              value={fmtMoney(pick?.price_target_upside)}
              tone={1}
            />
            <Stat
              label="Downside target"
              value={fmtMoney(pick?.price_target_downside)}
              tone={-1}
            />
          </dl>
        </Reveal>
      )}

      {pick?.catalyst_description && (
        <Reveal as="section" className="space-y-5">
          <h2 className="text-eyebrow">
            Catalyst · {pick.catalyst_category ?? "—"}
          </h2>
          <p className="text-body text-foreground/85 leading-relaxed">
            {pick.catalyst_description}
          </p>
        </Reveal>
      )}

      <Reveal as="section" className="space-y-5">
        <h2 className="text-eyebrow">Insider posture · last 90 days</h2>
        {insider.length === 0 ? (
          <p className="text-small text-muted-foreground">
            No Form 4 events on file in the last 90 days.
          </p>
        ) : (
          <dl className="grid gap-6 sm:grid-cols-3">
            <Stat label="Directional buys" value={buys} />
            <Stat label="Directional sells" value={sells} />
            <Stat
              label="Mechanical / other"
              value={insider.length - buys - sells}
              muted
            />
          </dl>
        )}
        <p className="text-caption">
          Codes P / S / V counted as directional; A / F / G / M / X excluded
          as mechanical (grants, withholding, gifts, derivative exercises).
        </p>
      </Reveal>

      {pick?.position_size_guidance && (
        <Reveal as="section" className="space-y-5">
          <h2 className="text-eyebrow">Position size guidance</h2>
          <p className="text-body text-foreground/85 leading-relaxed">
            {pick.position_size_guidance}
          </p>
        </Reveal>
      )}
    </div>
  )
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—"
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(2)}%`
}

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return "—"
  return `$${Number(v).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

function BackLink() {
  return (
    <Link
      href="/dashboard/focus-list"
      className="inline-flex items-center gap-1 text-caption transition-premium hover:text-foreground"
    >
      ← Focus list
    </Link>
  )
}

function Stat({
  label,
  value,
  muted,
  tone,
}: {
  label: string
  value: React.ReactNode
  muted?: boolean
  tone?: number
}) {
  const colorCls =
    tone != null
      ? tone > 0
        ? "text-gain"
        : tone < 0
        ? "text-loss"
        : "text-foreground"
      : muted
      ? "text-muted-foreground"
      : "text-foreground"
  return (
    <div className="space-y-1">
      <dt className="text-caption uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </dt>
      <dd className={`text-data text-h3 ${colorCls}`}>{value}</dd>
    </div>
  )
}
