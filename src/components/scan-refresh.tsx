"use client"

import type { ReactNode } from "react"
import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { Loader2, RefreshCw } from "lucide-react"

export type ScanStatus = {
  status: "idle" | "running" | "failed"
  latest_scan_completed_at: string | null
  running_since: string | null
  next_available_at: string | null
  last_error: string | null
}

/**
 * Shared-scan refresh control. The displayed scan is GLOBAL and cached -- this
 * only *requests* a fresher one when eligible; the actual scan runs in CI.
 * State is derived from /api/scan/status, which it polls while a scan runs.
 */
export function ScanRefresh({ initial }: { initial: ScanStatus }) {
  const router = useRouter()
  const [state, setState] = useState<ScanStatus>(initial)
  const [now, setNow] = useState(() => Date.now())
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const lastCompletedRef = useRef(initial.latest_scan_completed_at)

  // 1s ticker drives the live countdown + "started Xm ago".
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch("/api/scan/status", { cache: "no-store" })
      if (!r.ok) return
      const s: ScanStatus = await r.json()
      setState(s)
      // A newer scan finished -> pull the fresh server-rendered data in.
      if (
        s.status !== "running" &&
        s.latest_scan_completed_at &&
        s.latest_scan_completed_at !== lastCompletedRef.current
      ) {
        lastCompletedRef.current = s.latest_scan_completed_at
        router.refresh()
      }
    } catch {
      /* ignore transient poll errors */
    }
  }, [router])

  // Poll only while a scan is running.
  useEffect(() => {
    if (state.status !== "running") return
    const t = setInterval(fetchStatus, 20000)
    return () => clearInterval(t)
  }, [state.status, fetchStatus])

  const nextMs = state.next_available_at
    ? new Date(state.next_available_at).getTime()
    : 0
  const tooSoon = nextMs > now
  const running = state.status === "running"
  const failed = state.status === "failed"

  const onClick = useCallback(async () => {
    setBusy(true)
    setMsg(null)
    try {
      const r = await fetch("/api/scan/trigger", { method: "POST" })
      const j = await r.json().catch(() => ({}))
      if (j.status === "started" || j.status === "already_running") {
        setState((s) => ({
          ...s,
          status: "running",
          running_since: j.since ?? new Date().toISOString(),
        }))
        fetchStatus()
      } else if (j.status === "too_soon") {
        setState((s) => ({ ...s, next_available_at: j.next_available_at }))
        setMsg("A fresher scan isn't available yet.")
      } else if (j.error) {
        setMsg(
          j.error === "GH_DISPATCH_TOKEN not configured"
            ? "Manual refresh isn't configured yet."
            : "Couldn't start the scan — try again shortly.",
        )
      }
    } catch {
      setMsg("Couldn't start the scan — try again shortly.")
    } finally {
      setBusy(false)
    }
  }, [fetchStatus])

  function countdown(toMs: number): string {
    const ms = Math.max(0, toMs - now)
    const h = Math.floor(ms / 3_600_000)
    const m = Math.floor((ms % 3_600_000) / 60_000)
    const s = Math.floor((ms % 60_000) / 1000)
    if (h > 0) return `${h}h ${m}m`
    if (m > 0) return `${m}m ${s}s`
    return `${s}s`
  }

  const disabled = busy || running || (tooSoon && !failed)

  let label: ReactNode
  if (running) {
    const since = state.running_since
      ? new Date(state.running_since).getTime()
      : now
    const mins = Math.max(0, Math.round((now - since) / 60_000))
    label = (
      <>
        <Loader2 className="size-3.5 animate-spin" /> Scanning… (started {mins}m
        ago)
      </>
    )
  } else if (failed) {
    label = (
      <>
        <RefreshCw className="size-3.5" /> Last scan failed — Retry
      </>
    )
  } else if (tooSoon) {
    label = (
      <>
        <RefreshCw className="size-3.5" /> Next scan in {countdown(nextMs)}
      </>
    )
  } else {
    label = (
      <>
        <RefreshCw className="size-3.5" /> Refresh scan
      </>
    )
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-busy={running || busy}
        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 text-caption font-medium text-foreground transition-premium hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
      >
        {label}
      </button>
      {running && (
        <span className="text-caption text-muted-foreground">
          Showing the previous scan until this one finishes.
        </span>
      )}
      {msg && <span className="text-caption text-muted-foreground">{msg}</span>}
    </div>
  )
}
