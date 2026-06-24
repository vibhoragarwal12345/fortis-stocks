// Small "last scanned" freshness indicator, mirroring the Today board's
// data-freshness card. Server component: the relative time is computed at
// render, so the pages that use it are force-dynamic. Pass one row for a
// single timestamp (emerging) or several for a multi-clock read (commodities:
// short-term vs long-term).

type Row = {
  label: string
  /** ISO timestamp of the last run for this clock, or null if it never ran. */
  iso: string | null
  /** A dot pulses "live" while the run is younger than this. Defaults to ~26h. */
  freshWithinMs?: number
}

function timeAgo(iso: string | null, now: number): string {
  if (!iso) return "—"
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return "—"
  const m = Math.round((now - t) / 60000)
  if (m < 1) return "just now"
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

function fmtTimestamp(iso: string | null): string {
  if (!iso) return "Not yet run"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return "—"
  return d.toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  })
}

export function LastScanned({ rows }: { rows: Row[] }) {
  const now = Date.now()
  return (
    <div className="space-y-2 rounded-lg border border-border bg-card px-4 py-3 text-small shadow-[var(--shadow-card)]">
      {rows.map((r) => {
        const fresh =
          r.iso != null &&
          now - new Date(r.iso).getTime() < (r.freshWithinMs ?? 26 * 60 * 60 * 1000)
        return (
          <div key={r.label}>
            <p className="flex items-center gap-2 font-medium text-foreground">
              <span aria-hidden className="relative flex size-1.5">
                {fresh && (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-highlight opacity-60" />
                )}
                <span
                  className={`relative inline-flex size-1.5 rounded-full ${
                    fresh ? "bg-highlight" : "bg-muted-foreground"
                  }`}
                />
              </span>
              {r.label} {timeAgo(r.iso, now)}
            </p>
            <p className="pl-3.5 text-caption text-data text-muted-foreground">
              {fmtTimestamp(r.iso)}
            </p>
          </div>
        )
      })}
    </div>
  )
}
