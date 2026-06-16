import { createClient } from "@/lib/supabase/server"
import { Card, CardContent } from "@/components/ui/card"
import { Reveal } from "@/components/ui/reveal"

export const metadata = { title: "Emerging Watchlist — Fortis" }
export const dynamic = "force-dynamic"

type WatchRow = {
  id: number
  ticker: string
  conviction_tier:
    | "tier_1_high_conviction"
    | "tier_2_promising"
    | "tier_3_speculative"
    | null
  multibagger_score: number | null
  added_date: string
  entry_market_cap: number | null
  current_market_cap: number | null
  status: string
  thesis_id: number | null
  notes: string | null
}

type Thesis = {
  id: number
  ticker: string
  selection_rationale: string | null
  business_model: string | null
  the_10x_path: string | null
  moat_assessment: string | null
  founder_assessment: string | null
  what_has_to_go_right: string | null
  what_kills_it: string | null
  key_metrics_to_track: string[] | null
  risk_rating: string | null
  verification_score: number | null
  quality_grade: string | null
  generated_at: string
}

type Candidate = {
  ticker: string
  trait_scores: Record<string, number> | null
  multibagger_score: number | null
  screen_date: string
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

// The 6 multibagger-DNA traits the screener scores — these ALWAYS exist (the
// screener computed them), so they anchor every dossier's "why it's here".
const TRAITS: [string, string][] = [
  ["small_base", "Small base"],
  ["durable_growth", "Durable growth"],
  ["improving_unit_econ", "Improving unit econ"],
  ["large_tam_proxy", "Large TAM"],
  ["aligned_owners", "Aligned owners"],
  ["under_discovered", "Under-discovered"],
]

function money(v: number | null): string {
  if (!v || v <= 0) return "—"
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`
  return `$${v.toFixed(0)}`
}
/** Treat near-empty / boilerplate-only section text as missing. */
function hasText(s: string | null | undefined): s is string {
  return !!s && s.trim().length > 12
}

export default async function EmergingPage() {
  const supabase = await createClient()

  const { data: wl } = await supabase
    .from("emerging_watchlist")
    .select(
      "id, ticker, conviction_tier, multibagger_score, added_date, entry_market_cap, current_market_cap, status, thesis_id, notes",
    )
    .in("status", ["active", "thesis_intact", "thesis_weakening"])
    .order("multibagger_score", { ascending: false, nullsFirst: false })

  const rows = (wl ?? []) as WatchRow[]
  const tickers = rows.map((r) => r.ticker)

  // Join the full dossier (multibagger_theses) + the DNA trait scores
  // (multibagger_candidates) BY TICKER. Joining by ticker (not the watchlist's
  // stored thesis_id) self-heals: the freshest run shows the moment it lands.
  const [thesesRes, candsRes] = await Promise.all([
    tickers.length
      ? supabase
          .from("multibagger_theses")
          .select(
            "id, ticker, selection_rationale, business_model, the_10x_path, moat_assessment, founder_assessment, what_has_to_go_right, what_kills_it, key_metrics_to_track, risk_rating, verification_score, quality_grade, generated_at",
          )
          .in("ticker", tickers)
          .order("generated_at", { ascending: false })
      : Promise.resolve({ data: [] as Thesis[] }),
    tickers.length
      ? supabase
          .from("multibagger_candidates")
          .select("ticker, trait_scores, multibagger_score, screen_date")
          .in("ticker", tickers)
          .order("screen_date", { ascending: false })
      : Promise.resolve({ data: [] as Candidate[] }),
  ])

  // Best thesis per ticker: prefer one carrying a written selection rationale
  // (the theory-forward format); among those, the newest wins.
  const thesisByTicker = new Map<string, Thesis>()
  for (const t of (thesesRes.data ?? []) as Thesis[]) {
    const cur = thesisByTicker.get(t.ticker)
    if (!cur) {
      thesisByTicker.set(t.ticker, t)
    } else if (!hasText(cur.selection_rationale) && hasText(t.selection_rationale)) {
      thesisByTicker.set(t.ticker, t)
    }
  }
  const candByTicker = new Map<string, Candidate>()
  for (const c of (candsRes.data ?? []) as Candidate[]) {
    if (!candByTicker.has(c.ticker)) candByTicker.set(c.ticker, c) // latest screen
  }

  // Invariant: a name displays only if it can produce at least the
  // selection-rationale dossier (theory) OR its DNA trait scores. A truly
  // blank name is hidden.
  const display = rows.filter((r) => {
    const t = thesisByTicker.get(r.ticker)
    const c = candByTicker.get(r.ticker)
    return hasText(t?.selection_rationale) || hasText(t?.business_model) ||
      !!(c?.trait_scores && Object.keys(c.trait_scores).length)
  })

  display.sort((a, b) => {
    const t =
      (TIER_ORDER[a.conviction_tier ?? ""] ?? 9) -
      (TIER_ORDER[b.conviction_tier ?? ""] ?? 9)
    if (t !== 0) return t
    return (b.multibagger_score ?? 0) - (a.multibagger_score ?? 0)
  })

  // Curate to a focused, high-conviction set. The weekly discovery adds EVERY
  // qualifying tier-1/tier-2 name (+ strong tier-3), so the raw list balloons
  // (32 as of June 2026) and reads as noise to a client. Show only the top
  // dozen by conviction tier then score; the rest stay tracked in the DB.
  // Adjust MAX_SHOWN to taste.
  const MAX_SHOWN = 12
  const shown = display.slice(0, MAX_SHOWN)

  const grouped: Record<string, WatchRow[]> = {}
  for (const r of shown) {
    const k = r.conviction_tier ?? "tier_3_speculative"
    ;(grouped[k] = grouped[k] ?? []).push(r)
  }

  return (
    <div className="mx-auto max-w-[1280px] space-y-16 px-6 py-14 md:space-y-20 md:px-10 md:py-16">
      <Reveal as="header" className="space-y-3">
        <p className="text-eyebrow">Emerging watchlist · long horizon</p>
        <h1 className="text-h1">Structured speculation.</h1>
        <p className="text-body-lg max-w-[680px] text-muted-foreground">
          Small-cap multibagger candidates tracked over years. Researched
          honestly, risk-framed relentlessly. Updated weekly. Each name leads
          with the theory for why our screen surfaced it.
        </p>
      </Reveal>

      <Reveal>
        <div className="rounded-lg border border-warning/25 bg-warning/5 p-5">
          <p className="text-eyebrow text-warning">Risk framing</p>
          <p className="mt-2 text-small text-muted-foreground leading-relaxed">
            Emerging candidates are speculative, long-horizon, high-risk
            positions. Most will not become multibaggers. Position sizing should
            reflect the asymmetric risk: small bets, long horizons, expect many
            failures and a few large winners. For licensed advisor review — not
            investment advice.
          </p>
        </div>
      </Reveal>

      {display.length === 0 && (
        <Reveal>
          <Card>
            <CardContent className="py-16 text-center text-small text-muted-foreground">
              No candidates with a complete dossier yet. The weekly discovery
              run will populate this — names appear only once they carry a
              fact-checked thesis.
            </CardContent>
          </Card>
        </Reveal>
      )}

      {(
        ["tier_1_high_conviction", "tier_2_promising", "tier_3_speculative"] as const
      )
        .filter((tier) => grouped[tier])
        .map((tier) => (
          <section key={tier} className="space-y-6">
            <Reveal>
              <h2 className="text-eyebrow">{TIER_LABEL[tier]}</h2>
            </Reveal>
            <ul className="space-y-5">
              {grouped[tier]!.map((r, idx) => (
                <Reveal key={r.id} as="li" delay={Math.min(idx, 5) * 60}>
                  <EmergingCard
                    row={r}
                    thesis={thesisByTicker.get(r.ticker)}
                    cand={candByTicker.get(r.ticker)}
                  />
                </Reveal>
              ))}
            </ul>
          </section>
        ))}
    </div>
  )
}

function EmergingCard({
  row,
  thesis,
  cand,
}: {
  row: WatchRow
  thesis: Thesis | undefined
  cand: Candidate | undefined
}) {
  const traits = cand?.trait_scores ?? null
  const metrics = (thesis?.key_metrics_to_track ?? []).filter(
    (m) => typeof m === "string" && m.trim().length > 0,
  )
  return (
    <Card size="sm">
      <CardContent className="space-y-6 pt-5">
        {/* ── Top row ─────────────────────────────────────────────── */}
        <div className="flex items-start justify-between gap-6">
          <div className="space-y-1">
            <p className="font-mono text-[24px] font-semibold tracking-tight">
              {row.ticker}
            </p>
            <p className="text-caption tabular-nums">
              Added {row.added_date} · entry {money(row.entry_market_cap)} · score{" "}
              <span className="text-foreground">
                {row.multibagger_score?.toFixed(1) ?? "—"}
              </span>
              {thesis?.risk_rating && (
                <>
                  {" · "}
                  <span className="uppercase tracking-[0.12em]">
                    {thesis.risk_rating.replace(/_/g, " ")}
                  </span>
                </>
              )}
            </p>
          </div>
          {/* Return-since-added / peak / max-DD intentionally NOT shown here:
              the emerging list refreshes weekly while a multibagger thesis
              plays out over YEARS, so a since-added move would be noise and
              invite short-term reactions. Long-horizon tracking lives in the
              outcomes tracker, not on this card. (Owner directive, June 2026.) */}
        </div>

        {/* ── WHY IT'S HERE — the selection theory leads every dossier ─ */}
        <div className="rounded-lg border border-highlight/25 bg-highlight/5 p-4">
          <p className="text-eyebrow text-highlight">Why it&rsquo;s here</p>
          <p className="mt-1.5 text-body text-foreground/90 leading-relaxed">
            {hasText(thesis?.selection_rationale)
              ? thesis!.selection_rationale
              : "Surfaced by the structural screen on the DNA traits below. A full written thesis is still being assembled for this name."}
          </p>
        </div>

        {/* ── The 6 DNA trait scores — always available ───────────── */}
        {traits && Object.keys(traits).length > 0 && (
          <div className="space-y-2">
            <p className="text-caption uppercase tracking-[0.14em] text-muted-foreground">
              Multibagger DNA · screener scores
            </p>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-3">
              {TRAITS.map(([key, label]) => {
                const v = traits[key]
                return (
                  <div key={key} className="space-y-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <dt className="text-caption text-muted-foreground">{label}</dt>
                      <dd className="text-data text-[13px] text-foreground">
                        {typeof v === "number" ? v.toFixed(0) : "—"}
                      </dd>
                    </div>
                    <span className="block h-1 overflow-hidden rounded-full bg-secondary">
                      <span
                        className="block h-full rounded-full bg-highlight/70"
                        style={{
                          width: `${Math.max(2, Math.min(100, typeof v === "number" ? v : 0))}%`,
                        }}
                      />
                    </span>
                  </div>
                )
              })}
            </dl>
          </div>
        )}

        {hasText(thesis?.business_model) && (
          <Block label="Business model">{thesis!.business_model}</Block>
        )}
        {hasText(thesis?.the_10x_path) && (
          <Block label="The 10x path">{thesis!.the_10x_path}</Block>
        )}

        <div className="grid gap-6 sm:grid-cols-2">
          <Block label="Moat" tone="foreground">
            {hasText(thesis?.moat_assessment)
              ? thesis!.moat_assessment
              : "Limited data — moat not verifiable from connected sources for this under-covered name."}
          </Block>
          <Block label="Founder & alignment" tone="foreground">
            {hasText(thesis?.founder_assessment)
              ? thesis!.founder_assessment
              : "Limited data — founder/ownership detail not available from connected sources."}
          </Block>
          {hasText(thesis?.what_has_to_go_right) && (
            <Block label="What has to go right">{thesis!.what_has_to_go_right}</Block>
          )}
          <Block label="What kills it" tone="destructive">
            {hasText(thesis?.what_kills_it)
              ? thesis!.what_kills_it
              : "Limited data — specific failure modes not yet documented for this name."}
          </Block>
        </div>

        {metrics.length > 0 && (
          <div className="space-y-2">
            <p className="text-caption uppercase tracking-[0.14em] text-muted-foreground">
              Key metrics to track
            </p>
            <ul className="flex flex-wrap gap-2 text-caption">
              {metrics.map((m, i) => (
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

        {row.notes && (
          <p className="text-caption">Last review: {row.notes}</p>
        )}
      </CardContent>
    </Card>
  )
}

function Block({
  label,
  children,
  tone,
}: {
  label: string
  children: React.ReactNode
  tone?: "foreground" | "destructive"
}) {
  const labelCls =
    tone === "destructive"
      ? "text-destructive"
      : tone === "foreground"
      ? "text-foreground"
      : "text-muted-foreground"
  return (
    <div className="space-y-1.5">
      <p className={`text-caption uppercase tracking-[0.14em] ${labelCls}`}>
        {label}
      </p>
      <p className="text-small text-muted-foreground leading-relaxed">{children}</p>
    </div>
  )
}
