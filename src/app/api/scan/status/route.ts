import { NextResponse } from "next/server"

import { createClient } from "@/lib/supabase/server"

// Polled by the dashboard (~every 15-30s) while a scan runs, and once on load
// to drive the refresh control. Reads the shared scan_state singleton.
export const runtime = "nodejs"
export const dynamic = "force-dynamic"

const FRESHNESS_MS = 2 * 60 * 60 * 1000 // 2h freshness gate

export async function GET() {
  const sb = await createClient()
  const {
    data: { user },
  } = await sb.auth.getUser()
  if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 })

  const { data, error } = await sb
    .from("scan_state")
    .select(
      "latest_scan_id, latest_scan_completed_at, current_status, running_since, last_error",
    )
    .eq("id", 1)
    .maybeSingle()

  if (error) {
    return NextResponse.json(
      { error: "state read failed", detail: error.message },
      { status: 500 },
    )
  }

  const completedAt = data?.latest_scan_completed_at ?? null
  const nextAvailableAt = completedAt
    ? new Date(new Date(completedAt).getTime() + FRESHNESS_MS).toISOString()
    : null

  return NextResponse.json({
    status: data?.current_status ?? "idle",
    latest_scan_id: data?.latest_scan_id ?? null,
    latest_scan_completed_at: completedAt,
    running_since: data?.running_since ?? null,
    next_available_at: nextAvailableAt,
    last_error: data?.last_error ?? null,
  })
}
