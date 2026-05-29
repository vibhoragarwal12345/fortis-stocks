import Link from "next/link"

import { createClient } from "@/lib/supabase/server"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

export const metadata = { title: "Focus list — Fortis" }
export const dynamic = "force-dynamic"

type Row = {
  ticker: string
  rank: number | null
  conviction_grade: "A" | "B" | "C" | "D" | null
  composite_score: number | null
  conviction_score_adjusted: number | null
  catalyst_category: string | null
  catalyst_description: string | null
  thesis: string | null
  run_date: string
  run_type: string
}

const GRADE_BADGE: Record<string, "success" | "info" | "warning" | "neutral"> = {
  A: "success",
  B: "info",
  C: "warning",
  D: "neutral",
}

export default async function FocusListPage() {
  const supabase = await createClient()

  // Latest run we have, regardless of "today" status — surfaces real data
  // even on weekends/holidays when the cron didn't run.
  const { data: latestRow } = await supabase
    .from("ranked_focus_list")
    .select("run_date")
    .order("run_date", { ascending: false })
    .limit(1)
    .maybeSingle()

  if (!latestRow) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-10">
        <h1 className="text-2xl font-semibold tracking-tight">Focus list</h1>
        <Card className="mt-6">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            No focus list generated yet. Picks appear here after the next
            pre-market or midday pipeline run on a weekday.
          </CardContent>
        </Card>
      </div>
    )
  }

  const latest = latestRow.run_date

  const { data: rows } = await supabase
    .from("ranked_focus_list")
    .select(
      "ticker,rank,conviction_grade,composite_score,conviction_score_adjusted,catalyst_category,catalyst_description,thesis,run_date,run_type",
    )
    .eq("run_date", latest)
    .order("rank", { ascending: true })
    .limit(50)

  const picks = (rows ?? []) as Row[]
  const grouped: Record<string, Row[]> = {}
  for (const r of picks) {
    const g = r.conviction_grade ?? "D"
    ;(grouped[g] = grouped[g] ?? []).push(r)
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-10 space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Focus list</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Latest ranked picks from {latest}. {picks.length} names across A / B / C grades.
        </p>
      </div>

      {(["A", "B", "C", "D"] as const).map((grade) => {
        const list = grouped[grade] ?? []
        if (list.length === 0) return null
        return (
          <section key={grade}>
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
              Grade {grade} · {list.length} {list.length === 1 ? "pick" : "picks"}
            </h2>
            <div className="grid gap-3 md:grid-cols-2">
              {list.slice(0, 24).map((r) => (
                <Card key={`${grade}-${r.ticker}`} size="sm">
                  <CardHeader>
                    <div className="flex items-baseline justify-between gap-2">
                      <CardTitle className="font-mono text-base">
                        <Link
                          href={`/dashboard/research/${r.ticker}`}
                          className="hover:underline underline-offset-4"
                        >
                          {r.ticker}
                        </Link>
                      </CardTitle>
                      <Badge variant={GRADE_BADGE[grade] ?? "neutral"}>
                        {grade}
                      </Badge>
                    </div>
                    <CardDescription className="text-xs">
                      {r.catalyst_category ?? "—"} · rank #{r.rank ?? "—"} · score{" "}
                      {r.composite_score != null
                        ? Number(r.composite_score).toFixed(1)
                        : "—"}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="text-sm text-muted-foreground">
                    <p className="line-clamp-3">
                      {r.catalyst_description || r.thesis || "No catalyst detail attached."}
                    </p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
