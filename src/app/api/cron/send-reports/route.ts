import { NextRequest, NextResponse } from "next/server"

import { sendReport } from "@/lib/email"
import { createServiceClient } from "@/lib/supabase/service"

// juice + cheerio require Node APIs; opt out of Edge runtime.
export const runtime = "nodejs"
export const dynamic = "force-dynamic"

// How far back to look for undelivered reports. 90 min covers the gap
// between the composer running (around the brief's nominal time) and a
// cron firing on the same schedule with some slack.
const RECENT_MINUTES = 90

type ReportLite = {
  id: string
  report_type: "premarket" | "midday" | "close"
  report_date: string
  tenant_id: string | null
}

export async function GET(req: NextRequest) {
  const secret = process.env.CRON_SECRET
  if (!secret) {
    return NextResponse.json(
      { error: "CRON_SECRET not configured" },
      { status: 500 }
    )
  }
  // Accept either `Authorization: Bearer <secret>` or `x-cron-secret: <secret>`.
  const auth = req.headers.get("authorization") ?? ""
  const xcs = req.headers.get("x-cron-secret") ?? ""
  if (auth !== `Bearer ${secret}` && xcs !== secret) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 })
  }

  // ?dry=1 runs the full discovery + selection path but stops short of
  // hitting Resend (and skips the email_delivery_log insert + reports flip).
  // Use it to smoke the wiring against a real DB without spending credits.
  const dryRun = req.nextUrl.searchParams.get("dry") === "1"

  const sb = createServiceClient()
  const since = new Date(Date.now() - RECENT_MINUTES * 60_000).toISOString()

  const { data: reports, error } = await sb
    .from("reports")
    .select("id,report_type,report_date,tenant_id")
    .gte("generated_at", since)
    .eq("delivered_email", false)

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
  if (!reports?.length) {
    return NextResponse.json({
      attempted: 0,
      succeeded: 0,
      failed: 0,
      skipped: 0,
      reports: 0,
    })
  }

  const users = await listConfirmedUsers(sb)
  if (!users.length) {
    return NextResponse.json({
      attempted: 0,
      succeeded: 0,
      failed: 0,
      skipped: 0,
      reports: reports.length,
      users: 0,
      note: "no confirmed users to deliver to",
    })
  }

  let attempted = 0
  let succeeded = 0
  let failed = 0
  let skipped = 0
  let finalised = 0
  const details: unknown[] = []

  for (const r of reports as ReportLite[]) {
    let transientFailureForReport = false
    for (const u of users) {
      attempted++
      const res = await sendReport(r.id, u.id, { dryRun })
      if (res.ok) {
        succeeded++
      } else if (isSkipReason(res.reason)) {
        skipped++
      } else {
        failed++
        transientFailureForReport = true
      }
      details.push({
        report_id: r.id,
        report_type: r.report_type,
        user_email: u.email,
        ok: res.ok,
        reason: res.reason,
        message_id: res.messageId,
        bytes: res.bytes,
        truncated: res.truncated,
        already_delivered: res.alreadyDelivered ?? false,
      })
    }
    // Only flip the report's delivered_email flag once every confirmed
    // user has a terminal outcome (success or permanent skip). Transient
    // failures leave it false so the next cron tick can retry them. Dry
    // runs never mutate the row.
    if (!transientFailureForReport && !dryRun) {
      await sb
        .from("reports")
        .update({
          delivered_email: true,
          email_delivered_at: new Date().toISOString(),
        })
        .eq("id", r.id)
      finalised++
    }
  }

  return NextResponse.json({
    attempted,
    succeeded,
    failed,
    skipped,
    finalised,
    reports: reports.length,
    users: users.length,
    details,
  })
}

function isSkipReason(reason: string | undefined): boolean {
  if (!reason) return false
  return (
    reason.startsWith("unsubscribed") ||
    reason.includes("bounced") ||
    reason.startsWith("urgent-only")
  )
}

async function listConfirmedUsers(
  sb: ReturnType<typeof createServiceClient>
): Promise<{ id: string; email: string }[]> {
  const out: { id: string; email: string }[] = []
  let page = 1
  // Walk the admin pagination until we get a short page.
  while (true) {
    const { data, error } = await sb.auth.admin.listUsers({
      page,
      perPage: 100,
    })
    if (error) break
    const users = data?.users ?? []
    for (const u of users) {
      if (u.email_confirmed_at && u.email) {
        out.push({ id: u.id, email: u.email })
      }
    }
    if (users.length < 100) break
    page++
    if (page > 100) break // safety cap
  }
  return out
}
