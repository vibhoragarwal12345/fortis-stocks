import Link from "next/link"

import { createClient } from "@/lib/supabase/server"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

const GRADE_VARIANT = { A: "gradeA", B: "gradeB", C: "gradeC", D: "neutral" } as const

export const metadata = { title: "Research · Christopher Edwards Financial Associates" }
export const dynamic = "force-dynamic"

type PriceRef = {
  low: number | null
  mid: number | null
  high: number | null
  basis: number | null
  horizon_days: number | null
  method: string | null
  distribution: string | null
  calibration: number | null
}

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
  price_reference: PriceRef | null
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

  // The dossier: the most recent row that actually carries deep analysis.
  // Two bugs hid targets before this:
  //   1. Ordering by run_date+run_type is ambiguous — 3 scans/day share a
  //      run_date and the same run_type, so ties resolved arbitrarily.
  //      scan_id is the true lineage key.
  //   2. The newest shortlist row often has NO deep analysis (the deep
  //      agents process the ranking top-down until the run budget is
  //      spent), which blanked the bull/bear/targets even when a complete
  //      dossier existed from an earlier scan.
  const PICK_SELECT =
    "ticker,run_date,run_type,conviction_grade,composite_score,conviction_score_adjusted,thesis,catalyst_category,catalyst_description,bull_case,bear_case,price_reference,position_size_guidance"

  // Only a COMPLETE, fact-checked dossier shows prose — that's the product
  // rule (dossier_gate). An incomplete name falls through to market-metrics
  // only, below.
  const { data: deepRow } = await supabase
    .from("ranked_focus_list")
    .select(PICK_SELECT)
    .eq("ticker", ticker)
    .eq("dossier_complete", true)
    .order("scan_id", { ascending: false, nullsFirst: false })
    .limit(1)
    .maybeSingle()

  // Prose (thesis / bull / bear / price band / catalyst / sizing) renders ONLY
  // from a COMPLETE, fact-checked dossier — the gate's invariant must hold on a
  // direct URL too, not just in the nav.
  const pick = deepRow as Pick | null

  // Header fallback: with no complete dossier, still surface the latest
  // grade/score so the page isn't bare. This row is NEVER used for prose.
  let headerPick = pick
  if (!headerPick) {
    const { data: anyRow } = await supabase
      .from("ranked_focus_list")
      .select(
        "ticker,run_date,run_type,conviction_grade,composite_score,conviction_score_adjusted",
      )
      .eq("ticker", ticker)
      .order("scan_id", { ascending: false, nullsFirst: false })
      .limit(1)
      .maybeSingle()
    headerPick = anyRow as Pick | null
  }
  // No complete dossier (incomplete, or a non-shortlist name): show market
  // metrics + an honest note instead of partial, unverified prose.
  const deepPending = pick == null

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
    .select("transaction_code,is_directional_signal,transaction_date")
    .eq("ticker", ticker)
    // Server component: per-request "now" for the 90-day window. (Columns are
    // transaction_date/insider/role on form4_transactions — NOT filing_date.)
    .gte(
      "transaction_date",
      // eslint-disable-next-line react-hooks/purity
      new Date(Date.now() - 90 * 86_400_000).toISOString().slice(0, 10),
    )
    .order("transaction_date", { ascending: false })
    .limit(50)

  const insider = (form4 ?? []) as Array<{
    transaction_code: string | null
    is_directional_signal: boolean
    transaction_date: string
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

  if (!headerPick && !scan) {
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
  const headerRunDate = headerPick?.run_date ?? "—"
  const headerRunType = headerPick?.run_type ?? "scan"

  return (
    <div className="mx-auto max-w-[1120px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <BackLink />
        <p className="text-eyebrow">
          Research · {headerRunDate} {headerRunType}
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <h1 className="text-data text-display">{ticker}</h1>
          {headerPick?.conviction_grade && (
            <Badge
              variant={GRADE_VARIANT[headerPick.conviction_grade]}
              className="translate-y-1 text-[12px]"
            >
              Grade {headerPick.conviction_grade}
            </Badge>
          )}
        </div>
        <p className="text-small text-muted-foreground">
          Composite score{" "}
          <span className="text-data text-foreground">
            {headerPick?.composite_score != null
              ? Number(headerPick.composite_score).toFixed(1)
              : "—"}
          </span>
          {headerPick?.conviction_score_adjusted != null && (
            <>
              {" · conviction-adjusted "}
              <span className="text-data text-foreground">
                {Number(headerPick.conviction_score_adjusted).toFixed(1)}
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

      {deepPending && (
        <Reveal as="section">
          <div className="rounded-lg border border-warning/25 bg-warning/5 p-5">
            <p className="text-eyebrow text-warning">No published dossier</p>
            <p className="mt-2 text-small text-muted-foreground leading-relaxed">
              {ticker} isn&rsquo;t on the current focus list with a complete,
              fact-checked dossier, so only its live market metrics are shown.
              A name appears with a full thesis, bull / bear cases and a price
              reference range once a scan produces, and fact-checks, the
              complete dossier.
            </p>
          </div>
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

      {pick?.price_reference && (
        <Reveal as="section" className="space-y-5">
          <h2 className="text-eyebrow">
            Price reference range ·{" "}
            {pick.price_reference.horizon_days ?? "—"}-day Monte Carlo
          </h2>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-5 sm:grid-cols-4">
            <Stat
              label="Lower · 5th pct"
              value={fmtMoney(pick.price_reference.low)}
              tone={-1}
            />
            <Stat label="Median" value={fmtMoney(pick.price_reference.mid)} />
            <Stat
              label="Upper · 95th pct"
              value={fmtMoney(pick.price_reference.high)}
              tone={1}
            />
            <Stat
              label="Anchored on"
              value={fmtMoney(pick.price_reference.basis)}
              muted
            />
          </dl>
          <p className="text-caption">
            Statistical reference range:{" "}
            <span className="text-foreground">not a forecast</span>. A zero-drift
            Monte Carlo simulation over{" "}
            {pick.price_reference.horizon_days ?? "—"} trading days, calibrated
            to this name&rsquo;s own return distribution
            {pick.price_reference.distribution === "student_t"
              ? " (Student-t, fat-tailed)"
              : pick.price_reference.distribution === "normal"
              ? " (normal)"
              : ""}
            . Drift is zeroed so the band reflects volatility, not recent
            momentum.
          </p>
        </Reveal>
      )}

      {pick?.catalyst_description &&
        (pick.catalyst_category ?? "none").toLowerCase() !== "none" &&
        !/there is no specific catalyst|MIXED_SIGNALS/i.test(
          pick.catalyst_description,
        ) && (
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
