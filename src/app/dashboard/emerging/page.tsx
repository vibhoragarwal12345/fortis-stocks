import { createClient } from "@/lib/supabase/server"
import { Card, CardContent } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

export const metadata = { title: "Emerging Watchlist — Fortis" }
export const dynamic = "force-dynamic"

type WatchlistRow = {
  id: number
  ticker: string
  added_date: string
  conviction_tier:
    | "tier_1_high_conviction"
    | "tier_2_promising"
    | "tier_3_speculative"
    | null
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
  tier_1_high_conviction: "Tier 1 · Higher conviction",
  tier_2_promising: "Tier 2 · Promising",
  tier_3_speculative: "Tier 3 · Speculative",
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
    const t =
      (TIER_ORDER[a.conviction_tier ?? ""] ?? 9) -
      (TIER_ORDER[b.conviction_tier ?? ""] ?? 9)
    if (t !== 0) return t
    return (b.multibagger_score ?? 0) - (a.multibagger_score ?? 0)
  })

  const grouped: Record<string, WatchlistRow[]> = {}
  for (const r of rows) {
    const k = r.conviction_tier ?? "tier_3_speculative"
    ;(grouped[k] = grouped[k] ?? []).push(r)
  }

  return (
    <div className="mx-auto max-w-[1280px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <p className="text-eyebrow">
          Emerging watchlist · long horizon
        </p>
        <h1 className="text-h1">Structured speculation.</h1>
        <p className="text-body-lg max-w-[680px] text-muted-foreground">
          Small-cap multibagger candidates tracked over years. Researched
          honestly, risk-framed relentlessly. Updated weekly.
        </p>
      </Reveal>

      <Reveal>
        <div className="rounded-lg border border-warning/25 bg-warning/5 p-5">
          <p className="text-eyebrow text-warning">Risk framing</p>
          <p className="mt-2 text-small text-muted-foreground leading-relaxed">
            Emerging candidates are speculative, long-horizon, high-risk
            positions. Most will not become multibaggers. Position sizing
            should reflect the asymmetric risk: small bets, long horizons,
            expect many failures and a few large winners. For licensed
            advisor review — not investment advice.
          </p>
          <dl className="mt-5 grid gap-4 text-small sm:grid-cols-3">
            <Tier label="Tier 1" body="Multibagger DNA across all six traits and a clean balance sheet." />
            <Tier label="Tier 2" body="Promising but at least one gap (margin trajectory, runway, or discovery)." />
            <Tier label="Tier 3" body="Speculative; the engine rates these honestly as long shots even on its own list." />
          </dl>
        </div>
      </Reveal>

      {rows.length === 0 && (
        <Reveal>
          <Card>
            <CardContent className="py-16 text-center text-small text-muted-foreground">
              No candidates on the watchlist yet. The next monthly screen
              will populate it.
            </CardContent>
          </Card>
        </Reveal>
      )}

      {(
        [
          "tier_1_high_conviction",
          "tier_2_promising",
          "tier_3_speculative",
        ] as const
      )
        .filter((tier) => grouped[tier])
        .map((tier) => (
          <section key={tier} className="space-y-6">
            <Reveal>
              <h2 className="text-eyebrow">
                {TIER_LABEL[tier]}
              </h2>
            </Reveal>
            <ul className="space-y-5">
              {grouped[tier]!.map((r, idx) => (
                <Reveal key={r.id} as="li" delay={Math.min(idx, 5) * 60}>
                  <Card size="sm">
                    <CardContent className="space-y-6 pt-5">
                      {/* ── Top row ─────────────────────────────────── */}
                      <div className="flex items-start justify-between gap-6">
                        <div className="space-y-1">
                          <p className="font-mono text-[24px] font-semibold tracking-tight">
                            {r.ticker}
                          </p>
                          <p className="text-caption tabular-nums">
                            Added {r.added_date} · entry{" "}
                            {money(r.entry_market_cap)} · score{" "}
                            <span className="text-foreground">
                              {r.multibagger_score?.toFixed(1) ?? "—"}
                            </span>{" "}
                            ·{" "}
                            <span className="uppercase tracking-[0.14em]">
                              {r.status.replace(/_/g, " ")}
                            </span>
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="text-caption uppercase tracking-[0.14em] text-muted-foreground">
                            Return since added
                          </p>
                          <p
                            className={`text-data text-h2 ${
                              (r.return_since_added ?? 0) >= 0
                                ? "text-gain"
                                : "text-loss"
                            }`}
                          >
                            {pct(r.return_since_added)}
                          </p>
                          <p className="text-caption tabular-nums">
                            peak {pct(r.peak_return_pct)} · max DD{" "}
                            {pct(r.max_drawdown_pct)}
                          </p>
                        </div>
                      </div>

                      {r.thesis_summary && (
                        <p className="text-body text-foreground/90 leading-relaxed">
                          {r.thesis_summary}
                        </p>
                      )}

                      <div className="grid gap-6 sm:grid-cols-2">
                        {r.the_10x_path && (
                          <div className="space-y-2">
                            <p className="text-caption uppercase tracking-[0.14em] text-foreground">
                              The 10x path
                            </p>
                            <p className="text-small text-muted-foreground leading-relaxed">
                              {r.the_10x_path}
                            </p>
                          </div>
                        )}
                        {r.what_kills_it && (
                          <div className="space-y-2">
                            <p className="text-caption uppercase tracking-[0.14em] text-destructive">
                              What kills it
                            </p>
                            <p className="text-small text-muted-foreground leading-relaxed">
                              {r.what_kills_it}
                            </p>
                          </div>
                        )}
                      </div>

                      {r.key_metrics_to_track && r.key_metrics_to_track.length > 0 && (
                        <div className="space-y-2">
                          <p className="text-caption uppercase tracking-[0.14em] text-muted-foreground">
                            Key metrics to track
                          </p>
                          <ul className="flex flex-wrap gap-2 text-caption">
                            {r.key_metrics_to_track.map((m, i) => (
                              <li
                                key={i}
                                className="rounded-md border border-border px-2 py-1 text-foreground/80"
                              >
                                {m}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {r.notes && (
                        <p className="text-caption">Last review: {r.notes}</p>
                      )}
                    </CardContent>
                  </Card>
                </Reveal>
              ))}
            </ul>
          </section>
        ))}
    </div>
  )
}

function Tier({ label, body }: { label: string; body: string }) {
  return (
    <div className="space-y-1">
      <dt className="text-caption uppercase tracking-[0.14em] text-foreground">
        {label}
      </dt>
      <dd className="text-small text-muted-foreground leading-snug">{body}</dd>
    </div>
  )
}
