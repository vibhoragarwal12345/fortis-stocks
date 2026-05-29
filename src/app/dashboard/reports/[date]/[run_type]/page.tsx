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

export const metadata = { title: "Report — Fortis" }
export const dynamic = "force-dynamic"

type Report = {
  id: string
  report_type: string
  report_date: string
  content_html: string | null
  content_markdown: string | null
  a_grade_count: number | null
  b_grade_count: number | null
  c_grade_count: number | null
  avg_verification_score: number | null
  generated_at: string | null
}

const RUN_TYPE_LABEL: Record<string, string> = {
  premarket: "Pre-market brief",
  midday: "Midday brief",
  close: "Close brief",
  monthly_emerging: "Emerging opportunities (monthly)",
  quarterly_thesis_review: "Quarterly thesis review",
}

export default async function ReportPage({
  params,
}: {
  params: Promise<{ date: string; run_type: string }>
}) {
  const { date, run_type } = await params
  const supabase = await createClient()

  const { data } = await supabase
    .from("reports")
    .select(
      "id,report_type,report_date,content_html,content_markdown,a_grade_count,b_grade_count,c_grade_count,avg_verification_score,generated_at",
    )
    .eq("report_date", date)
    .eq("report_type", run_type)
    .order("generated_at", { ascending: false })
    .limit(1)
    .maybeSingle()

  const report = data as Report | null

  if (!report) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <Link
          href="/dashboard"
          className="text-xs text-muted-foreground underline-offset-4 hover:underline"
        >
          ← Back to dashboard
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          {RUN_TYPE_LABEL[run_type] ?? run_type} — {date}
        </h1>
        <Card className="mt-6">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            No report exists for{" "}
            <code className="font-mono">{run_type}</code> on{" "}
            <code className="font-mono">{date}</code>. If you expected one, the
            pipeline may not have run that day — daily briefs run weekdays only.
          </CardContent>
        </Card>
      </div>
    )
  }

  const label = RUN_TYPE_LABEL[report.report_type] ?? report.report_type

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 space-y-6">
      <div>
        <Link
          href="/dashboard"
          className="text-xs text-muted-foreground underline-offset-4 hover:underline"
        >
          ← Back to dashboard
        </Link>
        <div className="mt-2 flex flex-wrap items-baseline gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{label}</h1>
          <span className="text-sm text-muted-foreground">{report.report_date}</span>
          {report.avg_verification_score != null && (
            <Badge variant="info">
              {(Number(report.avg_verification_score) * 100).toFixed(0)}% verified
            </Badge>
          )}
        </div>
        {(report.a_grade_count != null ||
          report.b_grade_count != null ||
          report.c_grade_count != null) && (
          <p className="mt-1 text-xs text-muted-foreground">
            {report.a_grade_count ?? 0} A · {report.b_grade_count ?? 0} B ·{" "}
            {report.c_grade_count ?? 0} C
          </p>
        )}
      </div>

      {report.content_html ? (
        <Card>
          <CardContent className="px-6 py-6">
            <div
              className="prose prose-sm max-w-none dark:prose-invert"
              dangerouslySetInnerHTML={{ __html: report.content_html }}
            />
          </CardContent>
        </Card>
      ) : report.content_markdown ? (
        <Card>
          <CardContent className="px-6 py-6">
            <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">
              {report.content_markdown}
            </pre>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            Report exists but content is empty. The composer likely failed to
            render — check pipeline logs for{" "}
            <code className="font-mono">report_composer.py</code> on this date.
          </CardContent>
        </Card>
      )}
    </div>
  )
}
