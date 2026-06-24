import Link from "next/link"

import { createClient } from "@/lib/supabase/server"
import { Card, CardContent } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

export const metadata = { title: "Report · Christopher Edwards Financial Associates" }
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
  weekly_emerging: "Emerging opportunities · weekly",
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
      <div className="mx-auto max-w-[720px] px-6 py-14 md:px-10 md:py-16 space-y-12">
        <BackLink />
        <header className="space-y-3">
          <p className="text-eyebrow">
            {RUN_TYPE_LABEL[run_type] ?? run_type}
          </p>
          <h1 className="text-h1">No report for {date}.</h1>
        </header>
        <Card>
          <CardContent className="py-16 text-center text-small text-muted-foreground">
            The pipeline may not have run that day. Daily briefs run
            weekdays only.
          </CardContent>
        </Card>
      </div>
    )
  }

  const label = RUN_TYPE_LABEL[report.report_type] ?? report.report_type
  const verifiedPct =
    report.avg_verification_score != null
      ? Math.round(Number(report.avg_verification_score) * 100)
      : null

  return (
    <div className="mx-auto max-w-[860px] space-y-12 px-6 py-14 md:space-y-16 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-4">
        <BackLink />
        <p className="text-eyebrow">
          {label} · {report.report_date}
        </p>
        <h1 className="text-h1">{label}.</h1>
        {(report.a_grade_count != null ||
          report.b_grade_count != null ||
          report.c_grade_count != null ||
          verifiedPct != null) && (
          <p className="text-small text-muted-foreground tabular-nums">
            <span className="font-medium text-foreground">
              {report.a_grade_count ?? 0} A
            </span>{" "}
            · {report.b_grade_count ?? 0} B · {report.c_grade_count ?? 0} C
            {verifiedPct != null && (
              <>
                {" · "}verified {verifiedPct}%
              </>
            )}
          </p>
        )}
        {/* Daily reports are deprecated under the lean rewrite. We keep
            the viewer alive so historical reports stay readable + email
            links from older notifications still resolve. */}
        <div className="rounded-md border border-border bg-secondary/40 px-4 py-3 text-small text-muted-foreground">
          <span className="text-foreground">Historical report.</span>{" "}
          Christopher Edwards Financial Associates no longer composes daily briefs. The platform refreshes
          the dashboard every 2 hours instead.{" "}
          <Link
            href="/dashboard"
            className="underline-offset-4 hover:text-foreground hover:underline"
          >
            See the latest scan →
          </Link>
        </div>
      </Reveal>

      <Reveal>
        {report.content_html ? (
          <article
            className="report-prose"
            dangerouslySetInnerHTML={{ __html: report.content_html }}
          />
        ) : report.content_markdown ? (
          <pre className="report-prose whitespace-pre-wrap font-sans text-body leading-relaxed">
            {report.content_markdown}
          </pre>
        ) : (
          <Card>
            <CardContent className="py-16 text-center text-small text-muted-foreground">
              Report exists but content is empty. The composer likely failed
              to render. Check pipeline logs for{" "}
              <code className="font-mono">report_composer.py</code> on this
              date.
            </CardContent>
          </Card>
        )}
      </Reveal>
    </div>
  )
}

function BackLink() {
  return (
    <Link
      href="/dashboard"
      className="inline-flex items-center gap-1 text-caption transition-premium hover:text-foreground"
    >
      ← Today
    </Link>
  )
}
