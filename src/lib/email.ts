import { Resend } from "resend"
import juice from "juice"
import * as cheerio from "cheerio"

import { createServiceClient } from "@/lib/supabase/service"

// Gmail clips messages above ~102 KB; aim well below so the unsubscribe
// footer is always visible.
const MAX_EMAIL_BYTES = 100_000

type RunType = "premarket" | "midday" | "close"

type ReportRow = {
  id: string
  report_type: RunType
  report_date: string
  content_html: string
  content_structured: unknown
  a_grade_count: number | null
  b_grade_count: number | null
  c_grade_count: number | null
  avg_verification_score: number | null
}

type PrefsRow = {
  user_id: string
  premarket_enabled: boolean
  midday_enabled: boolean
  close_enabled: boolean
  urgent_alerts_only: boolean
  unsubscribe_token: string
  bounced: boolean
}

export type SendReportResult = {
  ok: boolean
  reason?: string
  messageId?: string
  bytes?: number
  truncated?: boolean
  /** True when this (report,user) pair already had a successful row in
   *  email_delivery_log — the send was a no-op. Treated as a skip by cron. */
  alreadyDelivered?: boolean
}

// ══════════════════════════════════════════════════════════════════════════
// Public entry point
// ══════════════════════════════════════════════════════════════════════════

export async function sendReport(
  reportId: string,
  userId: string,
  opts: { dryRun?: boolean } = {}
): Promise<SendReportResult> {
  const sb = createServiceClient()

  // 1 ── Report ----------------------------------------------------------
  const { data: report, error: rErr } = await sb
    .from("reports")
    .select(
      "id,report_type,report_date,content_html,content_structured,a_grade_count,b_grade_count,c_grade_count,avg_verification_score"
    )
    .eq("id", reportId)
    .single<ReportRow>()
  if (rErr || !report) {
    return { ok: false, reason: rErr?.message ?? "report not found" }
  }

  // 2 ── User email (via admin API) -------------------------------------
  const { data: userData, error: uErr } = await sb.auth.admin.getUserById(
    userId
  )
  const email = userData?.user?.email
  if (uErr || !email) {
    return { ok: false, reason: uErr?.message ?? "no email for user" }
  }

  // 2b ── Idempotency: skip if this user already received this report ---
  // The cron route may re-poll the same report inside the 90-minute window
  // (e.g. after a partial failure across N users). A prior success row in
  // email_delivery_log is authoritative -- never double-send.
  const { data: priorOk } = await sb
    .from("email_delivery_log")
    .select("id")
    .eq("report_id", reportId)
    .eq("user_id", userId)
    .eq("success", true)
    .limit(1)
    .maybeSingle()
  if (priorOk) {
    return {
      ok: true,
      alreadyDelivered: true,
      reason: "already delivered",
    }
  }

  // 3 ── Preferences (upsert defaults + unsub token if missing) ---------
  const prefs = await getOrCreatePrefs(sb, userId)
  if (prefs.bounced) return { ok: false, reason: "user previously bounced" }
  if (!prefsEnabledFor(prefs, report.report_type)) {
    return { ok: false, reason: `unsubscribed from ${report.report_type}` }
  }

  const urgent = hasCriticalAlerts(report.content_structured)
  if (prefs.urgent_alerts_only && !urgent) {
    return { ok: false, reason: "urgent-only filter, no critical alerts" }
  }

  // 4 ── Build HTML (inline CSS + truncate to 102 KB) ------------------
  const { html, truncated } = prepareEmailHtml(report, prefs)
  const bytes = Buffer.byteLength(html, "utf-8")

  const subject = buildSubject(report, urgent)

  if (opts.dryRun) {
    return { ok: true, messageId: "dry-run", bytes, truncated }
  }

  // 5 ── Send via Resend with retry/backoff ----------------------------
  const apiKey = process.env.RESEND_API_KEY
  if (!apiKey) return { ok: false, reason: "RESEND_API_KEY not set" }
  const from =
    process.env.EMAIL_FROM_ADDRESS ??
    "Fortis Briefs <briefs@example.com>"
  const resend = new Resend(apiKey)

  let lastErr: string | null = null
  let messageId: string | null = null
  let attemptCount = 0
  for (let attempt = 1; attempt <= 3; attempt++) {
    attemptCount = attempt
    try {
      const res = await resend.emails.send({
        from,
        to: email,
        subject,
        html,
        headers: {
          "List-Unsubscribe": `<${unsubscribeUrl(prefs.unsubscribe_token)}>`,
          "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
      })
      if (res.error) throw new Error(JSON.stringify(res.error))
      messageId = res.data?.id ?? null
      lastErr = null
      break
    } catch (e: unknown) {
      lastErr = String((e as Error)?.message ?? e).slice(0, 500)
      if (attempt < 3) {
        // 1s, 2s backoff
        await new Promise(r => setTimeout(r, 1000 * 2 ** (attempt - 1)))
      }
    }
  }

  // 6 ── Audit log ------------------------------------------------------
  await sb.from("email_delivery_log").insert({
    report_id: report.id,
    user_id: userId,
    attempted_at: new Date().toISOString(),
    attempt_number: attemptCount,
    success: lastErr === null,
    resend_message_id: messageId,
    error_message: lastErr,
    bytes_sent: bytes,
    truncated,
    subject_line: subject,
  })

  if (lastErr) return { ok: false, reason: lastErr, bytes, truncated }

  // NOTE: reports.delivered_email / email_delivered_at are flipped by the
  // cron route once every confirmed user has a terminal outcome for this
  // report. Flipping it here per-user would hide the report from the next
  // cron tick before remaining users get a chance.

  return {
    ok: true,
    messageId: messageId ?? undefined,
    bytes,
    truncated,
  }
}

// ══════════════════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════════════════

async function getOrCreatePrefs(
  sb: ReturnType<typeof createServiceClient>,
  userId: string
): Promise<PrefsRow> {
  // Insert with defaults if absent. on_conflict + ignoreDuplicates ensures
  // we don't overwrite existing prefs.
  await sb
    .from("email_preferences")
    .upsert({ user_id: userId }, {
      onConflict: "user_id",
      ignoreDuplicates: true,
    })
  const { data } = await sb
    .from("email_preferences")
    .select("*")
    .eq("user_id", userId)
    .single<PrefsRow>()
  if (!data) {
    throw new Error("email_preferences upsert failed -- migration applied?")
  }
  return data
}

function prefsEnabledFor(p: PrefsRow, rt: RunType): boolean {
  if (rt === "premarket") return p.premarket_enabled
  if (rt === "midday") return p.midday_enabled
  return p.close_enabled
}

function hasCriticalAlerts(structured: unknown): boolean {
  const s = structured as { sections?: { slug?: string; critical_alert_count?: number; high_alert_count?: number }[] } | null
  const pr = s?.sections?.find(x => x.slug === "portfolio_review")
  if (!pr) return false
  return (pr.critical_alert_count ?? 0) > 0 || (pr.high_alert_count ?? 0) > 0
}

function buildSubject(r: ReportRow, urgent: boolean): string {
  const date = formatDate(r.report_date)
  const a = r.a_grade_count ?? 0
  let base: string
  if (r.report_type === "premarket") {
    base =
      a > 0
        ? `Pre-Market Brief — ${date} — ${a} A-grade ${pluralize("pick", a)}`
        : `Pre-Market Brief — ${date}`
  } else if (r.report_type === "midday") {
    base =
      a > 0
        ? `Midday Update — ${date} — ${a} A-grade ${pluralize("pick", a)}`
        : `Midday Update — ${date}`
  } else {
    base = `Close-of-Day — ${date} — tomorrow's setup`
  }
  return urgent ? `[URGENT REVIEW] ${base}` : base
}

function formatDate(d: string): string {
  return new Date(d + "T00:00:00Z").toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  })
}

function pluralize(s: string, n: number): string {
  return n === 1 ? s : s + "s"
}

function unsubscribeUrl(token: string): string {
  const base = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000"
  return `${base}/email/unsubscribe/${token}`
}

function dashboardReportUrl(r: ReportRow): string {
  const base = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000"
  return `${base}/dashboard/reports/${r.report_date}/${r.report_type}`
}

function prepareEmailHtml(
  r: ReportRow,
  prefs: PrefsRow
): { html: string; truncated: boolean } {
  const preheader = preheaderText(r)
  const footer = footerHtml(prefs.unsubscribe_token, r)
  let html = injectPreheaderAndFooter(r.content_html, preheader, footer)

  // Inline CSS via juice. Keep <style> tags around for clients that
  // support media queries (mobile responsiveness in 7.1's template).
  try {
    html = juice(html, {
      applyStyleTags: true,
      removeStyleTags: false,
      preserveMediaQueries: true,
    })
  } catch {
    // Fall through with un-juiced HTML rather than failing the send.
  }

  let truncated = false
  if (Buffer.byteLength(html, "utf-8") > MAX_EMAIL_BYTES) {
    html = truncateHtml(html, r)
    truncated = true
  }
  return { html, truncated }
}

function preheaderText(r: ReportRow): string {
  const a = r.a_grade_count ?? 0
  const b = r.b_grade_count ?? 0
  const c = r.c_grade_count ?? 0
  return `${a} A / ${b} B / ${c} C graded picks · ${formatDate(r.report_date)}`
}

function footerHtml(token: string, r: ReportRow): string {
  const addr = process.env.EMAIL_PHYSICAL_ADDRESS ?? "[Physical address pending]"
  const reportUrl = dashboardReportUrl(r)
  const unsub = unsubscribeUrl(token)
  return `<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:720px;margin:0 auto;background:#ffffff;">
    <tr><td style="padding:24px 32px 28px;border-top:1px solid #d4d4d4;font-family:Georgia,serif;font-size:11.5px;color:#999999;line-height:1.55;">
      <p style="margin:0 0 8px;">
        <a href="${reportUrl}" style="color:#0f3a5f;text-decoration:none;border-bottom:1px solid #cdd9e4;">Read the full brief in the dashboard →</a>
      </p>
      <p style="margin:0 0 8px;">
        You are receiving Fortis briefs because your firm has subscribed.
        <a href="${unsub}" style="color:#0f3a5f;text-decoration:none;border-bottom:1px solid #cdd9e4;">Manage preferences or unsubscribe</a>.
      </p>
      <p style="margin:0;">${escapeHtml(addr)}</p>
    </td></tr>
  </table>`
}

function injectPreheaderAndFooter(
  html: string,
  preheader: string,
  footer: string
): string {
  // Hidden preheader -- shows in the inbox preview, hidden in the body.
  const preDiv = `<div style="display:none;font-size:1px;color:transparent;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">${escapeHtml(preheader)}</div>`
  let out = html
  if (/<body[^>]*>/i.test(out)) {
    out = out.replace(/<body([^>]*)>/i, `<body$1>${preDiv}`)
  } else {
    out = preDiv + out
  }
  if (/<\/body>/i.test(out)) {
    out = out.replace(/<\/body>/i, `${footer}</body>`)
  } else {
    out = out + footer
  }
  return out
}

function truncateHtml(html: string, r: ReportRow): string {
  const $ = cheerio.load(html)
  // Highest-noise / lowest-priority sections come off first.
  const trimSlugs = [
    "weekly_themes",
    "institutional_flows",
    "after_hours_movers",
    "tonight_earnings",
    "volume_options",
    "pivot_stocks",
    "tomorrow_setup",
    "tomorrow_focus",
    "risk_radar",
    "honorable_mentions",
    "portfolio_day_pnl",
    "portfolio_daily_perf",
  ]
  for (const slug of trimSlugs) {
    $(`.sec-${slug}`).remove()
    if (currentBytes() < MAX_EMAIL_BYTES) return finalise()
  }
  $(".sec-supporting_picks").remove()
  if (currentBytes() < MAX_EMAIL_BYTES) return finalise()

  // Last resort: keep only the first three A-grade pick cards.
  const picks = $(".sec-focus_list .pick").toArray()
  for (let i = 3; i < picks.length; i++) $(picks[i]).remove()
  return finalise()

  function currentBytes(): number {
    return Buffer.byteLength($.html(), "utf-8")
  }
  function finalise(): string {
    const reportUrl = dashboardReportUrl(r)
    const banner = `<div style="background:#fff7ed;border:1px solid #fed7aa;color:#92400e;padding:12px 16px;margin:0 0 14px;font-size:13px;line-height:1.5;border-radius:4px;">
      This is a <strong>preview</strong> of today's full brief. Read the
      complete report — all picks, charts and verification details —
      <a href="${reportUrl}" style="color:#92400e;font-weight:bold;">in the dashboard</a>.
    </div>`
    const wrap = $(".wrap").first()
    if (wrap.length) {
      const firstSec = wrap.find("section").first()
      if (firstSec.length) firstSec.before(banner)
      else wrap.prepend(banner)
    }
    return $.html()
  }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[
      c
    ]!)
  )
}
