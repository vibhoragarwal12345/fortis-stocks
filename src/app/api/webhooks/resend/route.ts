import { NextRequest, NextResponse } from "next/server"
import * as crypto from "node:crypto"

import { createServiceClient } from "@/lib/supabase/service"

export const runtime = "nodejs"
export const dynamic = "force-dynamic"

/**
 * Resend webhook handler. Configure the endpoint in the Resend dashboard
 * pointing at /api/webhooks/resend; copy the signing secret into
 * RESEND_WEBHOOK_SECRET. Resend sends Svix-style HMAC-SHA256 signatures
 * (`svix-id`, `svix-timestamp`, `svix-signature` headers).
 *
 * Events handled: email.delivered, email.opened, email.clicked, email.bounced.
 * On hard bounce we flag email_preferences.bounced = true so the cron
 * route stops sending to the address.
 */
export async function POST(req: NextRequest) {
  const body = await req.text()
  const secret = process.env.RESEND_WEBHOOK_SECRET
  if (secret) {
    const id = req.headers.get("svix-id") ?? ""
    const ts = req.headers.get("svix-timestamp") ?? ""
    const sig = req.headers.get("svix-signature") ?? ""
    if (!verifySvixSignature(secret, id, ts, body, sig)) {
      return NextResponse.json({ error: "invalid signature" }, { status: 401 })
    }
  }
  // If no RESEND_WEBHOOK_SECRET configured, accept anyway (dev) but log.
  // In production you MUST set RESEND_WEBHOOK_SECRET.

  let evt: { type?: string; data?: Record<string, unknown> }
  try {
    evt = JSON.parse(body)
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 })
  }

  const type = evt.type
  const data = (evt.data ?? {}) as Record<string, unknown>
  const messageId = (data.email_id ?? data.id) as string | undefined

  if (!type || !messageId) {
    return NextResponse.json({ ok: true, ignored: "no type or message id" })
  }

  const sb = createServiceClient()
  const now = new Date().toISOString()
  const patch: Record<string, unknown> = {}
  switch (type) {
    case "email.delivered":
      patch.delivered_at = now
      break
    case "email.opened":
      patch.opened_at = now
      break
    case "email.clicked":
      patch.clicked_at = now
      break
    case "email.bounced":
      patch.bounced_at = now
      patch.bounce_type = (data.bounce_type ?? data.type ?? "unknown") as string
      break
    default:
      return NextResponse.json({ ok: true, ignored: type })
  }

  await sb
    .from("email_delivery_log")
    .update(patch)
    .eq("resend_message_id", messageId)

  // Hard bounce -> disable the user's prefs so future cron runs skip them.
  if (type === "email.bounced") {
    const bt = String(data.bounce_type ?? data.type ?? "").toLowerCase()
    if (bt.includes("hard") || bt.includes("perm")) {
      const { data: row } = await sb
        .from("email_delivery_log")
        .select("user_id")
        .eq("resend_message_id", messageId)
        .limit(1)
        .maybeSingle()
      if (row?.user_id) {
        await sb
          .from("email_preferences")
          .update({
            bounced: true,
            bounced_at: now,
            bounce_reason: String(data.reason ?? "hard bounce").slice(0, 500),
            updated_at: now,
          })
          .eq("user_id", row.user_id)
      }
    }
  }

  return NextResponse.json({ ok: true })
}

// ── Svix signature verification ─────────────────────────────────────────
// svix-signature header format: "v1,<base64sig> v1,<base64sig> ..."
// Body to sign: `${id}.${ts}.${rawBody}`
// Secret: usually `whsec_<base64>`; the prefix is stripped, the rest is
// base64-decoded to bytes used as the HMAC key.

function verifySvixSignature(
  secret: string,
  id: string,
  ts: string,
  body: string,
  sigHeader: string
): boolean {
  if (!id || !ts || !sigHeader) return false
  let keyBytes: Buffer
  if (secret.startsWith("whsec_")) {
    try {
      keyBytes = Buffer.from(secret.slice(6), "base64")
    } catch {
      return false
    }
  } else {
    keyBytes = Buffer.from(secret, "utf-8")
  }
  const toSign = `${id}.${ts}.${body}`
  const expected = crypto.createHmac("sha256", keyBytes).update(toSign).digest("base64")
  const candidates = sigHeader
    .split(" ")
    .map(s => s.replace(/^v\d+,/, ""))
    .filter(Boolean)
  return candidates.some(s => timingSafeStrEq(s, expected))
}

function timingSafeStrEq(a: string, b: string): boolean {
  const aBuf = Buffer.from(a)
  const bBuf = Buffer.from(b)
  if (aBuf.length !== bBuf.length) return false
  try {
    return crypto.timingSafeEqual(aBuf, bBuf)
  } catch {
    return false
  }
}
