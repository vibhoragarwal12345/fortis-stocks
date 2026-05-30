import Link from "next/link"

import { createClient } from "@/lib/supabase/server"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

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

const GRADE_LABEL: Record<string, string> = {
  A: "Grade A — high conviction",
  B: "Grade B — promising",
  C: "Grade C — watch list",
  D: "Grade D — passed on",
}

export default async function FocusListPage() {
  const supabase = await createClient()

  const { data: latestRow } = await supabase
    .from("ranked_focus_list")
    .select("run_date")
    .order("run_date", { ascending: false })
    .limit(1)
    .maybeSingle()

  if (!latestRow) {
    return (
      <div className="mx-auto max-w-[1280px] px-6 py-14 md:px-10 md:py-16">
        <header className="space-y-2">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Focus list
          </p>
          <h1 className="text-h1">No picks yet.</h1>
        </header>
        <Card className="mt-12">
          <CardContent className="py-16 text-center text-small text-muted-foreground">
            Picks appear here after the next pre-market or midday pipeline
            run on a weekday.
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
    <div className="mx-auto max-w-[1280px] space-y-20 px-6 py-14 md:space-y-24 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
          Focus list · {latest}
        </p>
        <h1 className="text-h1">{picks.length} names ranked today.</h1>
        <p className="text-body-lg max-w-[640px] text-muted-foreground">
          Conviction-graded picks across A / B / C tiers. Click into any
          ticker for the full thesis, catalyst, insider posture, and bull /
          bear cases.
        </p>
      </Reveal>

      {(["A", "B", "C", "D"] as const).map((grade) => {
        const list = grouped[grade] ?? []
        if (list.length === 0) return null
        return (
          <section key={grade} className="space-y-6">
            <Reveal>
              <div className="flex items-baseline justify-between gap-4">
                <h2 className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
                  {GRADE_LABEL[grade]}
                </h2>
                <span className="text-caption tabular-nums">
                  {list.length} {list.length === 1 ? "pick" : "picks"}
                </span>
              </div>
            </Reveal>
            <div className="grid gap-5 md:grid-cols-2">
              {list.slice(0, 24).map((r, idx) => (
                <Reveal key={`${grade}-${r.ticker}`} delay={Math.min(idx, 6) * 60}>
                  <Link
                    href={`/dashboard/research/${r.ticker}`}
                    className="group block h-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-xl"
                  >
                    <Card
                      size="sm"
                      className="h-full transition-premium duration-300 group-hover:-translate-y-0.5 group-hover:shadow-[var(--shadow-md)]"
                    >
                      <CardHeader>
                        <div className="flex items-baseline justify-between gap-3">
                          <CardTitle className="font-mono text-[20px] font-semibold tracking-tight">
                            {r.ticker}
                          </CardTitle>
                          <span className="text-caption uppercase tracking-[0.14em] text-foreground">
                            {grade}
                          </span>
                        </div>
                        <p className="text-caption tabular-nums">
                          {r.catalyst_category ?? "—"} · rank #{r.rank ?? "—"} ·
                          score{" "}
                          {r.composite_score != null
                            ? Number(r.composite_score).toFixed(1)
                            : "—"}
                        </p>
                      </CardHeader>
                      <CardContent>
                        <p className="line-clamp-3 text-small text-muted-foreground">
                          {r.catalyst_description ||
                            r.thesis ||
                            "No catalyst detail attached."}
                        </p>
                      </CardContent>
                    </Card>
                  </Link>
                </Reveal>
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
