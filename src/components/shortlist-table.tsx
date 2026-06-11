"use client"

import * as React from "react"
import { useRouter } from "next/navigation"

import { cn } from "@/lib/utils"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

// The terminal core: the latest scan's shortlist as a premium data table
// with one-tap signal filters. Client component — rows arrive serialized
// from the server page; filtering is instant and local.

export type ShortlistRow = {
  ticker: string
  rank: number | null
  score: number | null
  price: number | null
  dayChangePct: number | null
  gapPct: number | null
  ret20Pct: number | null
  relVol: number | null
  rsi: number | null
  fromHighPct: number | null
  breakout: boolean
  breakdown: boolean
}

type SignalKey =
  | "all"
  | "breakout"
  | "breakdown"
  | "volsurge"
  | "oversold"
  | "overbought"

const SIGNALS: { key: SignalKey; label: string; test: (r: ShortlistRow) => boolean }[] = [
  { key: "all",        label: "All",          test: () => true },
  { key: "breakout",   label: "Breakout",     test: (r) => r.breakout },
  { key: "volsurge",   label: "Vol surge",    test: (r) => r.relVol != null && r.relVol >= 2 },
  { key: "oversold",   label: "Oversold",     test: (r) => r.rsi != null && r.rsi <= 30 },
  { key: "overbought", label: "Overbought",   test: (r) => r.rsi != null && r.rsi >= 70 },
  { key: "breakdown",  label: "Breakdown",    test: (r) => r.breakdown },
]

// Signal → chip tone. Color is information here: green momentum, red
// breakdowns, cyan volume, amber RSI extremes.
const SIGNAL_TONE: Record<string, string> = {
  breakout: "border-gain/30 text-gain",
  breakdown: "border-loss/30 text-loss",
  "vol surge": "border-highlight/30 text-highlight",
  oversold: "border-warning/30 text-warning",
  overbought: "border-warning/30 text-warning",
}

function rowSignals(r: ShortlistRow): string[] {
  const flags: string[] = []
  if (r.breakout) flags.push("breakout")
  if (r.breakdown) flags.push("breakdown")
  if (r.relVol != null && r.relVol >= 2) flags.push("vol surge")
  if (r.rsi != null && r.rsi <= 30) flags.push("oversold")
  if (r.rsi != null && r.rsi >= 70) flags.push("overbought")
  return flags
}

const pct = (v: number | null, digits = 2) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`

function DeltaCell({
  value,
  className,
  ...props
}: { value: number | null } & React.ComponentProps<typeof TableCell>) {
  return (
    <TableCell
      numeric
      className={cn(
        value == null
          ? "text-muted-foreground"
          : value >= 0
            ? "text-gain"
            : "text-loss",
        className,
      )}
      {...props}
    >
      {pct(value)}
    </TableCell>
  )
}

export function ShortlistTable({ rows }: { rows: ShortlistRow[] }) {
  const router = useRouter()
  const [signal, setSignal] = React.useState<SignalKey>("all")

  const counts = React.useMemo(() => {
    const m = new Map<SignalKey, number>()
    for (const s of SIGNALS) m.set(s.key, rows.filter(s.test).length)
    return m
  }, [rows])

  const active = SIGNALS.find((s) => s.key === signal) ?? SIGNALS[0]
  const visible = rows.filter(active.test)

  const open = (ticker: string) => router.push(`/dashboard/research/${ticker}`)

  return (
    <div className="space-y-4">
      {/* Signal filter chips. Zero-count signals stay visible but disabled
          so the vocabulary of the system is always on screen. */}
      <div role="tablist" aria-label="Signal filter" className="flex flex-wrap gap-2">
        {SIGNALS.map((s) => {
          const count = counts.get(s.key) ?? 0
          const isActive = signal === s.key
          return (
            <button
              key={s.key}
              role="tab"
              aria-selected={isActive}
              disabled={count === 0 && s.key !== "all"}
              onClick={() => setSignal(s.key)}
              className={cn(
                // 44px touch target on mobile, denser on desktop.
                "inline-flex h-11 items-center gap-1.5 rounded-full border px-4 text-[12.5px] font-medium sm:h-8 sm:px-3.5",
                "transition-premium select-none disabled:opacity-35 disabled:pointer-events-none",
                isActive
                  ? "border-highlight/40 bg-highlight/10 text-highlight"
                  : "border-border bg-transparent text-muted-foreground hover:border-foreground/25 hover:text-foreground",
              )}
            >
              {s.label}
              <span className="text-data text-[11px] opacity-70">{count}</span>
            </button>
          )
        })}
      </div>

      <div className="overflow-hidden rounded-xl border border-border bg-card shadow-[var(--shadow-card)]">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-12 pl-5">#</TableHead>
              <TableHead>Ticker</TableHead>
              <TableHead numeric>Score</TableHead>
              <TableHead numeric>Price</TableHead>
              <TableHead numeric className="pr-5 sm:pr-4">1D</TableHead>
              <TableHead numeric className="hidden md:table-cell">Gap</TableHead>
              <TableHead numeric className="hidden md:table-cell">20D</TableHead>
              <TableHead numeric className="hidden lg:table-cell">Rel vol</TableHead>
              <TableHead numeric className="hidden lg:table-cell">RSI</TableHead>
              <TableHead numeric className="hidden xl:table-cell pr-5">52W hi</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.length === 0 && (
              <TableRow>
                <TableCell colSpan={10} className="py-12 text-center text-small text-muted-foreground">
                  No names carry this signal in the current scan.
                </TableCell>
              </TableRow>
            )}
            {visible.map((r, i) => (
              <TableRow
                key={r.ticker}
                interactive
                tabIndex={0}
                aria-label={`Open research for ${r.ticker}`}
                onClick={() => open(r.ticker)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    open(r.ticker)
                  }
                }}
                className="row-in focus-visible:outline-none focus-visible:bg-secondary/50"
                style={{ animationDelay: `${Math.min(i, 12) * 30}ms` }}
              >
                <TableCell className="pl-5 text-data text-muted-foreground">
                  {r.rank ?? "—"}
                </TableCell>
                <TableCell>
                  <span className="text-data font-medium">{r.ticker}</span>
                  <span className="ml-2.5 hidden gap-1 sm:inline-flex">
                    {rowSignals(r).map((f) => (
                      <span
                        key={f}
                        className={cn(
                          "rounded border px-1.5 py-px text-[10px] uppercase tracking-[0.1em]",
                          SIGNAL_TONE[f] ?? "border-border text-muted-foreground",
                        )}
                      >
                        {f}
                      </span>
                    ))}
                  </span>
                </TableCell>
                <TableCell numeric className="font-semibold">
                  <span className="inline-flex flex-col items-end gap-1">
                    {r.score != null ? r.score.toFixed(1) : "—"}
                    {r.score != null && (
                      <span
                        aria-hidden
                        className="block h-0.5 w-11 overflow-hidden rounded-full bg-secondary"
                      >
                        <span
                          className="bar-grow block h-full rounded-full bg-highlight/80"
                          style={{
                            width: `${Math.max(4, Math.min(100, r.score))}%`,
                            animationDelay: `${Math.min(i, 12) * 30 + 200}ms`,
                          }}
                        />
                      </span>
                    )}
                  </span>
                </TableCell>
                <TableCell numeric>
                  {r.price != null ? r.price.toFixed(2) : "—"}
                </TableCell>
                <DeltaCell value={r.dayChangePct} className="pr-5 sm:pr-4" />
                <DeltaCell value={r.gapPct} className="hidden md:table-cell" />
                <DeltaCell value={r.ret20Pct} className="hidden md:table-cell" />
                <TableCell numeric className="hidden lg:table-cell">
                  {r.relVol != null ? `${r.relVol.toFixed(2)}×` : "—"}
                </TableCell>
                <TableCell numeric className="hidden lg:table-cell">
                  {r.rsi != null ? r.rsi.toFixed(0) : "—"}
                </TableCell>
                <TableCell numeric className="hidden xl:table-cell pr-5 text-muted-foreground">
                  {pct(r.fromHighPct)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
