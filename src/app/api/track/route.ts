import { NextRequest, NextResponse } from "next/server"
import { z } from "zod"

import { createClient } from "@/lib/supabase/server"

// Cookies => SSR client => RLS gates the insert as the authenticated user.
export const runtime = "nodejs"
export const dynamic = "force-dynamic"

const Body = z.object({
  event_type: z
    .string()
    .min(1)
    .max(64)
    // event_type is a free-form text column on the DB side so that adding
    // a new event in src/lib/track.ts never needs a migration. We still
    // enforce a sane character set here to keep the table searchable and
    // free of injection-noise.
    .regex(/^[a-z][a-z0-9_]*$/, "lowercase snake_case only"),
  event_data: z.record(z.string(), z.unknown()).optional(),
})

export async function POST(req: NextRequest) {
  let parsed: z.infer<typeof Body>
  try {
    parsed = Body.parse(await req.json())
  } catch (err) {
    return NextResponse.json(
      { error: "invalid body", detail: String(err) },
      { status: 400 }
    )
  }

  const sb = await createClient()
  const {
    data: { user },
  } = await sb.auth.getUser()
  if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 })

  const { error } = await sb.from("dashboard_events").insert({
    user_id: user.id,
    event_type: parsed.event_type,
    event_data: parsed.event_data ?? {},
  })

  // 23505 = unique_violation. That means the 5-second dedup bucket caught a
  // duplicate -- exactly what the constraint exists to do -- so we treat it
  // as success. Anything else surfaces as a 500 so the client can retry.
  if (error && error.code !== "23505") {
    return NextResponse.json(
      { error: "insert failed", detail: error.message },
      { status: 500 }
    )
  }

  // Fire-and-forget contract: clients ignore the body, look only at status.
  return new NextResponse(null, { status: 204 })
}
