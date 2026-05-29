import Link from "next/link"

import { createClient } from "@/lib/supabase/server"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export const metadata = { title: "Emerging Watchlist — Fortis" }
export const dynamic = "force-dynamic"

type WatchlistRow = {
  id: number
  ticker: string
  added_date: string
  conviction_tier: "tier_1_high_conviction" | "tier_2_promising" | "tier_3_speculative" | null
  entry_price: number | null
  entry_market_cap: number | null
  thesis_summary: string | null
  the_10x_path: string | null
  what_kills_it: string | null
  key_metrics_to_track: string[] | null
  multibagger_score: number | null
  status: string
  current_price: number | null
  current_market_cap: number | null
  return_since_added: number | null
  peak_return_pct: number | null
  max_drawdown_pct: number | null
  notes: string | null
  thesis_id: number | null
}

const TIER_ORDER: Record<string, number> = {
  tier_1_high_conviction: 0,
  tier_2_promising: 1,
  tier_3_speculative: 2,
}

const TIER_LABEL: Record<string, string> = {
  tier_1_high_conviction: "Tier 1 — Higher conviction",
  tier_2_promising: "Tier 2 — Promising",
  tier_3_speculative: "Tier 3 — Speculative",
}

const TIER_HUE: Record<string, string> = {
  tier_1_high_conviction: "bg-emerald-50 border-emerald-200",
  tier_2_promising: "bg-sky-50 border-sky-200",
  tier_3_speculative: "bg-amber-50 border-amber-200",
}

const STATUS_HUE: Record<string, string> = {
  active: "bg-zinc-100 text-zinc-700",
  thesis_intact: "bg-emerald-100 text-emerald-800",
  thesis_weakening: "bg-amber-100 text-amber-800",
  thesis_broken: "bg-red-100 text-red-800",
  graduated: "bg-violet-100 text-violet-800",
  archived: "bg-zinc-200 text-zinc-600",
}

function pct(v: number | null): string {
  if (v === null || v === undefined) return "—"
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(1)}%`
}

function money(v: number | null): string {
  if (!v || v <= 0) return "—"
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`
  return `$${v.toFixed(0)}`
}

export default async function EmergingPage() {
  const supabase = await createClient()
  const { data } = await supabase
    .from("emerging_watchlist")
    .select(
      "id, ticker, added_date, conviction_tier, entry_price, entry_market_cap, thesis_summary, the_10x_path, what_kills_it, key_metrics_to_track, multibagger_score, status, current_price, current_market_cap, return_since_added, peak_return_pct, max_drawdown_pct, notes, thesis_id",
    )
    .order("added_date", { ascending: false })

  const rows = (data ?? []) as WatchlistRow[]
  rows.sort((a, b) => {
    const t = (TIER_ORDER[a.conviction_tier ?? ""] ?? 9) - (TIER_ORDER[b.conviction_tier ?? ""] ?? 9)
    if (t !== 0) return t
    return (b.multibagger_score ?? 0) - (a.multibagger_score ?? 0)
  })

  const grouped: Record<string, WatchlistRow[]> = {}
  for (const r of rows) {
    const k = r.conviction_tier ?? "tier_3_speculative"
    ;(grouped[k] = grouped[k] ?? []).push(r)
  }

  return (
    <div className="mx-auto max-w-6xl px-6 py-8 space-y-6">
      <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
        <div className="font-semibold tracking-tight">
          Emerging candidates are speculative, long-horizon, high-risk positions.
        </div>
        <div className="mt-1 leading-snug">
          Most will not become multibaggers. Position sizing should reflect the
          asymmetric risk: small bets, long horizons, expect many failures and
          a few large winners. For licensed advisor review — not investment
          advice.
        </div>
      </div>

      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Emerging Watchlist</h1>
          <p className="text-sm text-muted-foreground">
            Structured speculation. Researched honestly, risk-framed
            relentlessly. Updates monthly; thesis review quarterly.
          </p>
        </div>
        <Link
          href="/dashboard"
          className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          ← Back to daily
        </Link>
      </div>

      {rows.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            No candidates on the watchlist yet. The next monthly screen will
            populate it.
          </CardContent>
        </Card>
      )}

      {(["tier_1_high_conviction", "tier_2_promising", "tier_3_speculative"] as const)
        .filter((tier) => grouped[tier])
        .map((tier) => (
          <section key={tier} className="space-y-3">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
              {TIER_LABEL[tier]}
            </h2>
            <div className="grid gap-3">
              {grouped[tier]!.map((r) => (
                <Card key={r.id} className={TIER_HUE[tier]}>
                  <CardHeader className="pb-2">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <CardTitle className="flex items-baseline gap-2 text-lg">
                          <span className="font-mono">{r.ticker}</span>
                          <span
                            className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${
                              STATUS_HUE[r.status] ?? "bg-zinc-100 text-zinc-700"
                            }`}
                          >
                            {r.status.replace(/_/g, " ")}
                          </span>
                        </CardTitle>
                        <div className="mt-0.5 text-xs text-muted-foreground">
                          Added {r.added_date} · entry {money(r.entry_market_cap)}{" "}
                          · multibagger score{" "}
                          <span className="font-semibold text-foreground">
                            {r.multibagger_score?.toFixed(1) ?? "—"}
                          </span>
                        </div>
                      </div>
                      <div className="text-right text-sm">
                        <div className="text-xs text-muted-foreground">return since added</div>
                        <div
                          className={`text-xl font-semibold tabular-nums ${
                            (r.return_since_added ?? 0) >= 0
                              ? "text-emerald-700"
                              : "text-red-700"
                          }`}
                        >
                          {pct(r.return_since_added)}
                        </div>
                        <div className="text-[10px] text-muted-foreground">
                          peak {pct(r.peak_return_pct)} · max DD {pct(r.max_drawdown_pct)}
                        </div>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="grid gap-3 text-sm">
                    {r.thesis_summary && (
                      <div>
                        <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                          Thesis
                        </div>
                        <div className="text-foreground/90">{r.thesis_summary}</div>
                      </div>
                    )}
                    <div className="grid gap-3 sm:grid-cols-2">
                      {r.the_10x_path && (
                        <div>
                          <div className="text-[11px] font-semibold uppercase tracking-wider text-emerald-800">
                            The 10x path
                          </div>
                          <div className="text-foreground/85 leading-snug">
                            {r.the_10x_path}
                          </div>
                        </div>
                      )}
                      {r.what_kills_it && (
                        <div>
                          <div className="text-[11px] font-semibold uppercase tracking-wider text-red-800">
                            What kills it
                          </div>
                          <div className="text-foreground/85 leading-snug">
                            {r.what_kills_it}
                          </div>
                        </div>
                      )}
                    </div>
                    {r.key_metrics_to_track && r.key_metrics_to_track.length > 0 && (
                      <div>
                        <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                          Key metrics to track
                        </div>
                        <ul className="mt-0.5 flex flex-wrap gap-1">
                          {r.key_metrics_to_track.map((m, i) => (
                            <li
                              key={i}
                              className="rounded bg-white/70 px-2 py-0.5 text-xs text-foreground/80 border"
                            >
                              {m}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {r.notes && (
                      <div className="text-xs text-muted-foreground">
                        Last review: {r.notes}
                      </div>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          </section>
        ))}
    </div>
  )
}
