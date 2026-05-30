import Link from "next/link"

import { createClient } from "@/lib/supabase/server"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

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

  const { data: form4 } = await supabase
    .from("form4_transactions")
    .select(
      "transaction_code,is_directional_signal,filing_date,person_name,person_title",
    )
    .eq("ticker", ticker)
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

  if (!pick) {
    return (
      <div className="mx-auto max-w-[1120px] px-6 py-14 md:px-10 md:py-16 space-y-12">
        <BackLink />
        <header className="space-y-3">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Research
          </p>
          <h1 className="font-mono text-h1 tracking-tight">{ticker}</h1>
        </header>
        <Card>
          <CardContent className="py-16 text-center text-small text-muted-foreground">
            We have no pipeline-generated thesis for{" "}
            <code className="font-mono">{ticker}</code> yet. If you expected
            one, confirm the ticker is in the universe and that a recent
            pipeline run has executed.
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[1120px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <BackLink />
        <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
          Research · {pick.run_date} {pick.run_type}
        </p>
        <div className="flex items-baseline gap-4">
          <h1 className="font-mono text-display tracking-tight">{ticker}</h1>
          {pick.conviction_grade && (
            <span className="text-caption uppercase tracking-[0.14em] text-foreground">
              Grade {pick.conviction_grade}
            </span>
          )}
        </div>
        <p className="text-small text-muted-foreground">
          Composite score{" "}
          <span className="text-foreground tabular-nums">
            {pick.composite_score != null
              ? Number(pick.composite_score).toFixed(1)
              : "—"}
          </span>
        </p>
      </Reveal>

      {pick.thesis && (
        <Reveal as="section" className="space-y-5">
          <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Thesis
          </h2>
          <p className="text-body-lg whitespace-pre-line text-foreground/90">
            {pick.thesis}
          </p>
        </Reveal>
      )}

      <section className="grid gap-5 md:grid-cols-2">
        {pick.bull_case && (
          <Reveal>
            <Card size="sm" className="h-full">
              <CardHeader>
                <CardTitle className="text-[15px]">Bull case</CardTitle>
              </CardHeader>
              <CardContent className="text-small text-foreground/85 leading-relaxed">
                {pick.bull_case}
              </CardContent>
            </Card>
          </Reveal>
        )}
        {pick.bear_case && (
          <Reveal delay={80}>
            <Card size="sm" className="h-full">
              <CardHeader>
                <CardTitle className="text-[15px]">Bear case</CardTitle>
              </CardHeader>
              <CardContent className="text-small text-foreground/85 leading-relaxed">
                {pick.bear_case}
              </CardContent>
            </Card>
          </Reveal>
        )}
      </section>

      {pick.catalyst_description && (
        <Reveal as="section" className="space-y-5">
          <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Catalyst · {pick.catalyst_category ?? "—"}
          </h2>
          <p className="text-body text-foreground/85 leading-relaxed">
            {pick.catalyst_description}
          </p>
        </Reveal>
      )}

      <Reveal as="section" className="space-y-5">
        <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
          Insider posture · last 90 days
        </h2>
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

      {pick.position_size_guidance && (
        <Reveal as="section" className="space-y-5">
          <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Position size guidance
          </h2>
          <p className="text-body text-foreground/85 leading-relaxed">
            {pick.position_size_guidance}
          </p>
        </Reveal>
      )}
    </div>
  )
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
}: {
  label: string
  value: number
  muted?: boolean
}) {
  return (
    <div className="space-y-1">
      <dt className="text-caption uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </dt>
      <dd
        className={`text-h2 tabular-nums ${
          muted ? "text-muted-foreground" : "text-foreground"
        }`}
      >
        {value}
      </dd>
    </div>
  )
}
