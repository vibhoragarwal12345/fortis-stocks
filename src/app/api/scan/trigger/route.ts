import { NextResponse } from "next/server"

import { createClient } from "@/lib/supabase/server"
import { createServiceClient } from "@/lib/supabase/service"

// Triggers a market scan. The scan itself runs in GitHub Actions (minutes to
// ~30m) -- this endpoint only fires the workflow_dispatch and updates shared
// state; it NEVER runs the scan inside the request.
//
// Gating (the budget protection, keyed on the GLOBAL cached scan, not per
// user):
//   - already running  -> do nothing (one scan serves everyone)
//   - cached scan < 2h  -> too_soon (freshness gate)
// A 'running' state older than STALE_RUNNING_MS is treated as a crashed run
// and may be overridden, so a timed-out scan can never lock the button forever.
export const runtime = "nodejs"
export const dynamic = "force-dynamic"

const FRESHNESS_MS = 2 * 60 * 60 * 1000 // 2h
const STALE_RUNNING_MS = 70 * 60 * 1000 // workflow timeout is 50m; 70m = surely dead

export async function POST() {
  // 1. Require an authenticated user.
  const sb = await createClient()
  const {
    data: { user },
  } = await sb.auth.getUser()
  if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 })

  // 2. Read shared state (service role -- single source of truth).
  const svc = createServiceClient()
  const { data: state, error: readErr } = await svc
    .from("scan_state")
    .select("latest_scan_completed_at, current_status, running_since")
    .eq("id", 1)
    .maybeSingle()
  if (readErr) {
    return NextResponse.json(
      { error: "state read failed", detail: readErr.message },
      { status: 500 },
    )
  }

  const now = Date.now()
  const runningSince = state?.running_since
    ? new Date(state.running_since).getTime()
    : 0
  const completedAt = state?.latest_scan_completed_at
    ? new Date(state.latest_scan_completed_at).getTime()
    : 0

  // GATE 1: a scan is already running (and not stale) -> shared, do nothing.
  if (
    state?.current_status === "running" &&
    now - runningSince < STALE_RUNNING_MS
  ) {
    return NextResponse.json({
      status: "already_running",
      since: state.running_since,
    })
  }

  // GATE 2: cached scan is still fresh (< 2h) -> freshness/budget gate.
  if (completedAt && now - completedAt < FRESHNESS_MS) {
    return NextResponse.json({
      status: "too_soon",
      next_available_at: new Date(completedAt + FRESHNESS_MS).toISOString(),
    })
  }

  // 3. Atomically claim 'running'. The conditional WHERE guards the race
  //    between two users clicking at once: concurrent UPDATEs serialize in
  //    Postgres, so only the first matches and gets a row back.
  const staleCutoff = new Date(now - STALE_RUNNING_MS).toISOString()
  const nowIso = new Date(now).toISOString()
  const { data: claimed, error: claimErr } = await svc
    .from("scan_state")
    .update({
      current_status: "running",
      running_since: nowIso,
      triggered_by: "manual",
      last_error: null,
      updated_at: nowIso,
    })
    .eq("id", 1)
    .or(`current_status.neq.running,running_since.lt.${staleCutoff}`)
    .select("id")
  if (claimErr) {
    return NextResponse.json(
      { error: "claim failed", detail: claimErr.message },
      { status: 500 },
    )
  }
  if (!claimed || claimed.length === 0) {
    return NextResponse.json({
      status: "already_running",
      since: state?.running_since ?? null,
    })
  }

  // 4. Fire the workflow. Same market_scan.yml the cron uses -> identical
  //    pipeline, one source of truth.
  const token = process.env.GH_DISPATCH_TOKEN
  const repo = process.env.GH_REPO ?? "vibhoragarwal12345/fortis-stocks"
  if (!token) {
    // Don't leave the button stuck "running" if we can't actually dispatch.
    await svc
      .from("scan_state")
      .update({ current_status: "idle", running_since: null, updated_at: nowIso })
      .eq("id", 1)
    return NextResponse.json(
      { error: "GH_DISPATCH_TOKEN not configured" },
      { status: 500 },
    )
  }

  const ghRes = await fetch(
    `https://api.github.com/repos/${repo}/actions/workflows/market_scan.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "main", inputs: { scan_type: "manual" } }),
    },
  )

  if (!ghRes.ok) {
    const detail = await ghRes.text()
    await svc
      .from("scan_state")
      .update({
        current_status: "failed",
        running_since: null,
        last_error: `workflow dispatch failed: ${ghRes.status}`,
        updated_at: nowIso,
      })
      .eq("id", 1)
    return NextResponse.json(
      { error: "workflow dispatch failed", status_code: ghRes.status, detail },
      { status: 502 },
    )
  }

  return NextResponse.json({ status: "started" })
}
