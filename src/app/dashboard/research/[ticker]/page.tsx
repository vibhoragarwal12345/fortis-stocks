import Link from "next/link"
import { notFound } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

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

  // Last 90d insider posture (uses Form 4 directional filter from migration 037).
  const { data: form4 } = await supabase
    .from("form4_transactions")
    .select("transaction_code,is_directional_signal,filing_date,person_name,person_title")
    .eq("ticker", ticker)
    .gte(
      "filing_date",
      new Date(Date.now() - 90 * 86_400_000).toISOString(),
    )
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
    (i) => i.is_directional_signal && (i.transaction_code || "").toUpperCase() === "P",
  ).length
  const sells = insider.filter(
    (i) =>
      i.is_directional_signal &&
      ["S", "V"].includes((i.transaction_code || "").toUpperCase()),
  ).length

  if (!pick) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <Link
          href="/dashboard/focus-list"
          className="text-xs text-muted-foreground underline-offset-4 hover:underline"
        >
          ← Back to focus list
        </Link>
        <h1 className="mt-2 font-mono text-2xl font-semibold tracking-wider">
          {ticker}
        </h1>
        <Card className="mt-6">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            We have no pipeline-generated thesis for{" "}
            <code className="font-mono">{ticker}</code> yet. If you expected one,
            confirm the ticker is in the universe and that a recent pipeline
            run has executed.
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 space-y-6">
      <div className="space-y-1">
        <Link
          href="/dashboard/focus-list"
          className="text-xs text-muted-foreground underline-offset-4 hover:underline"
        >
          ← Back to focus list
        </Link>
        <div className="flex items-baseline gap-3">
          <h1 className="font-mono text-3xl font-semibold tracking-wider">
            {ticker}
          </h1>
          {pick.conviction_grade && (
            <Badge
              variant={
                pick.conviction_grade === "A"
                  ? "success"
                  : pick.conviction_grade === "B"
                  ? "info"
                  : pick.conviction_grade === "C"
                  ? "warning"
                  : "neutral"
              }
            >
              {pick.conviction_grade}
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          From {pick.run_date} {pick.run_type} run · composite score{" "}
          {pick.composite_score != null
            ? Number(pick.composite_score).toFixed(1)
            : "—"}
        </p>
      </div>

      {pick.thesis && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Thesis</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="whitespace-pre-line text-sm leading-relaxed">{pick.thesis}</p>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {pick.bull_case && (
          <Card size="sm" className="bg-emerald-50/40">
            <CardHeader>
              <CardTitle className="text-sm">Bull case</CardTitle>
            </CardHeader>
            <CardContent className="text-sm">{pick.bull_case}</CardContent>
          </Card>
        )}
        {pick.bear_case && (
          <Card size="sm" className="bg-rose-50/40">
            <CardHeader>
              <CardTitle className="text-sm">Bear case</CardTitle>
            </CardHeader>
            <CardContent className="text-sm">{pick.bear_case}</CardContent>
          </Card>
        )}
      </div>

      {pick.catalyst_description && (
        <Card size="sm">
          <CardHeader>
            <CardTitle className="text-sm">Catalyst</CardTitle>
            <CardDescription>{pick.catalyst_category ?? "—"}</CardDescription>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {pick.catalyst_description}
          </CardContent>
        </Card>
      )}

      <Card size="sm">
        <CardHeader>
          <CardTitle className="text-sm">Insider posture (last 90 days)</CardTitle>
          <CardDescription>
            Directional Form 4 events only (codes P / S / V; A / F / G / M / X
            excluded as mechanical).
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm">
          {insider.length === 0 ? (
            <span className="text-muted-foreground">
              No Form 4 events on file in the last 90 days.
            </span>
          ) : (
            <div className="flex flex-wrap gap-4">
              <span>
                <span className="font-semibold text-emerald-700">{buys}</span>{" "}
                directional buys
              </span>
              <span>
                <span className="font-semibold text-rose-700">{sells}</span>{" "}
                directional sells
              </span>
              <span className="text-muted-foreground">
                {insider.length - buys - sells} mechanical / other
              </span>
            </div>
          )}
        </CardContent>
      </Card>

      {pick.position_size_guidance && (
        <Card size="sm">
          <CardHeader>
            <CardTitle className="text-sm">Position size guidance</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">{pick.position_size_guidance}</CardContent>
        </Card>
      )}
    </div>
  )
}
