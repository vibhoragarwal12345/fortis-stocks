import Link from "next/link"

import { AtmosphereBackdrop } from "@/components/atmosphere-backdrop"
import { AuroraField } from "@/components/ui/aurora-field"
import { Badge } from "@/components/ui/badge"
import { buttonVariants } from "@/components/ui/button"
import { CountUp } from "@/components/ui/count-up"
import { GlowCard } from "@/components/ui/glow-card"
import { Parallax } from "@/components/ui/parallax"
import { Reveal } from "@/components/ui/reveal"
import { Section } from "@/components/ui/section"
import { SiteFooter } from "@/components/site-footer"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { TickerTape, type TapeItem } from "@/components/ui/ticker-tape"
import { WordReveal } from "@/components/ui/word-reveal"
import {
  formatScanTime,
  getLandingMarketData,
  type LandingPreviewRow,
} from "@/lib/landing-data"

export const metadata = {
  title: "Fortis Stock Intelligence",
  description:
    "Institutional research for wealth advisors. The full liquid US market scanned through every trading day, deep analysis on the names that earn it, conviction grades on what survives — every claim traced to its source.",
}

// Gateway. Not a marketing page: a full-fold aurora hero (nav + headline +
// tape fill exactly one viewport), then four restrained sections that give
// the scroll a deliberate arc — funnel, product preview, coverage, honesty.
//
// Market numbers are REAL — read from the latest completed scan and
// re-rendered at most every 15 minutes (ISR below). The hardcoded sets are
// a fallback for an empty database only, and are labeled as illustrative.

export const revalidate = 900

export default async function Home() {
  const live = await getLandingMarketData()
  const tapeItems = live?.tape.length ? live.tape : FALLBACK_TAPE
  const previewRows = live?.preview.length ? live.preview : FALLBACK_PREVIEW
  const scanBadge = live
    ? `Last updated ${formatScanTime(live.asOf)} · 15-min delayed`
    : "Last updated 09:21 ET · 15-min delayed"

  return (
    <main className="relative min-h-screen text-foreground">
      {/* Wall St signpost, dimmed, behind the whole gateway. Cursor glow is
          mounted globally in the root layout. */}
      <AtmosphereBackdrop variant="atmosphere" />
      {/* ─── Top bar ──────────────────────────────────────────── */}
      <header className="absolute inset-x-0 top-0 z-10">
        <div className="mx-auto flex h-16 max-w-[1120px] items-center justify-between px-6 md:px-10">
          <Link
            href="/"
            className="flex items-center gap-2.5 transition-premium hover:opacity-80"
          >
            <span aria-hidden className="size-2 rounded-[2px] bg-highlight" />
            <span className="text-[13px] font-semibold uppercase tracking-[0.22em]">
              Fortis
            </span>
          </Link>
          <Link
            href="/login"
            className={buttonVariants({ variant: "ghost", size: "sm" })}
          >
            Sign in
          </Link>
        </div>
      </header>

      {/* ─── Hero — one exact fold: bar, headline, tape ───────── */}
      <div className="relative overflow-hidden">
        {/* Aurora backdrop, receding at 0.35× scroll for depth. */}
        <Parallax speed={0.35} className="absolute inset-0">
          <AuroraField />
        </Parallax>

        <div className="relative flex min-h-svh flex-col">
          {/* pt-16 clears the absolute top bar; flex-1 centers the rest. */}
          <div className="flex flex-1 items-center pt-16">
            <div className="mx-auto w-full max-w-[1120px] px-6 py-16 md:px-10">
              <div className="max-w-[860px] space-y-8">
                <Reveal>
                  <p className="text-eyebrow flex items-center gap-2.5">
                    <span aria-hidden className="relative flex size-2">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-highlight opacity-60" />
                      <span className="relative inline-flex size-2 rounded-full bg-highlight" />
                    </span>
                    Fortis · Stock Intelligence
                  </p>
                </Reveal>
                <h1 className="text-display">
                  <WordReveal
                    as="span"
                    className="block"
                    text="Three thousand stocks."
                    delay={120}
                    step={90}
                  />
                  <WordReveal
                    as="span"
                    className="block text-muted-foreground"
                    text="Thirty convictions."
                    delay={450}
                    step={90}
                  />
                </h1>
                <Reveal delay={700}>
                  <p className="text-body-lg max-w-[620px] text-muted-foreground">
                    Fortis sweeps every liquid US ticker through the trading
                    day, spends deep analysis only where it&rsquo;s earned,
                    and grades what survives — every claim traced to its
                    source.
                  </p>
                </Reveal>
                <Reveal delay={820}>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-3 pt-2">
                    <Link href="/signup" className={buttonVariants({ size: "xl" })}>
                      Get started
                    </Link>
                    <Link
                      href="/login"
                      className={buttonVariants({ variant: "ghost", size: "xl" })}
                    >
                      Sign in
                    </Link>
                    <span className="text-small text-muted-foreground">
                      Free while in early access.
                    </span>
                  </div>
                </Reveal>
              </div>
            </div>
          </div>

          {/* Live tape closes the fold. */}
          <Reveal delay={950}>
            <TickerTape items={tapeItems} className="relative" />
          </Reveal>
        </div>
      </div>

      {/* ─── How it works ─────────────────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <Reveal as="header" className="mb-12 max-w-[680px]">
          <p className="text-eyebrow">How it works</p>
          <h2 className="mt-3 text-h1">
            A two-speed funnel. Fast on everything, deep on what matters.
          </h2>
        </Reveal>

        {/* Stat strip — the funnel in three numbers. */}
        <Reveal delay={100}>
          <dl className="mb-12 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-3">
            {STATS.map((s) => (
              <div key={s.label} className="bg-background px-5 py-5 md:px-7">
                <dt className="order-last mt-1 text-caption">{s.label}</dt>
                <dd className="text-data text-h2">
                  <CountUp value={s.value} suffix={s.suffix} />
                </dd>
              </div>
            ))}
          </dl>
        </Reveal>

        <ul className="grid gap-6 md:grid-cols-3">
          {FEATURES.map((f, i) => (
            <Reveal key={f.title} as="li" delay={160 + i * 100}>
              <GlowCard className="h-full rounded-xl border border-border bg-card p-8 shadow-[var(--shadow-card)] transition-premium duration-300 hover:-translate-y-0.5">
                <p className="text-eyebrow">
                  <span className="text-data text-highlight">{f.index}</span>
                  <span className="mx-2 text-border">·</span>
                  {f.eyebrow}
                </p>
                <h3 className="mt-4 text-h3">{f.title}</h3>
                <p className="mt-3 text-body text-muted-foreground">{f.body}</p>
              </GlowCard>
            </Reveal>
          ))}
        </ul>
      </Section>

      {/* ─── Product preview ──────────────────────────────────── */}
      <Section spacing="default" width="default">
        <Reveal as="header" className="mb-12 max-w-[640px]">
          <p className="text-eyebrow">The product</p>
          <h2 className="mt-3 text-h1">One live dashboard.</h2>
          <p className="mt-3 text-body text-muted-foreground">
            The latest scan, graded and ranked, the moment you sign in. Click
            any name for the full dossier — catalyst, insider posture, the
            debate, and the critic&rsquo;s verdict.
          </p>
        </Reveal>

        <Reveal delay={150}>
          <GlowCard className="overflow-hidden rounded-2xl border border-border bg-card shadow-[var(--shadow-lg)]">
            {/* Window chrome */}
            <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
              <div className="flex items-center gap-3">
                <span aria-hidden className="flex gap-1.5">
                  <span className="size-2.5 rounded-full bg-secondary" />
                  <span className="size-2.5 rounded-full bg-secondary" />
                  <span className="size-2.5 rounded-full bg-secondary" />
                </span>
                <span className="text-caption">Latest scan · complete</span>
              </div>
              <Badge variant="neutral">{scanBadge}</Badge>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-12 pl-6">#</TableHead>
                  <TableHead>Ticker</TableHead>
                  <TableHead>Grade</TableHead>
                  <TableHead numeric>Score</TableHead>
                  <TableHead numeric className="hidden sm:table-cell">
                    Price
                  </TableHead>
                  <TableHead numeric className="pr-6">1D</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {previewRows.map((r) => (
                  <TableRow key={r.rank} interactive={!r.masked}>
                    <TableCell className="pl-6 text-data text-muted-foreground">
                      {r.rank}
                    </TableCell>
                    <TableCell>
                      {r.masked ? (
                        <span
                          aria-label="Hidden — sign in to reveal"
                          className="text-data font-medium blur-[5px] select-none"
                        >
                          ▮▮▮▮
                        </span>
                      ) : (
                        <span className="text-data font-medium">{r.ticker}</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {r.grade ? (
                        <Badge variant={GRADE_VARIANT[r.grade]}>{r.grade}</Badge>
                      ) : (
                        <Badge variant="neutral">—</Badge>
                      )}
                    </TableCell>
                    <TableCell numeric>{r.score}</TableCell>
                    <TableCell numeric className="hidden sm:table-cell">
                      {r.masked ? (
                        <span aria-hidden className="blur-[5px] select-none">
                          000.00
                        </span>
                      ) : (
                        (r.price ?? "—")
                      )}
                    </TableCell>
                    <TableCell
                      numeric
                      className={`pr-6 ${
                        r.masked || r.change == null
                          ? "text-muted-foreground"
                          : r.change >= 0
                            ? "text-gain"
                            : "text-loss"
                      }`}
                    >
                      {r.masked ? (
                        <span aria-hidden className="blur-[5px] select-none">
                          +0.00%
                        </span>
                      ) : r.change == null ? (
                        "—"
                      ) : (
                        `${r.change >= 0 ? "+" : ""}${r.change.toFixed(2)}%`
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </GlowCard>
        </Reveal>
        <Reveal delay={250}>
          <p className="mt-4 text-caption">
            {live
              ? "Live from the latest market scan, 15-minute delayed. "
              : "Illustrative data. "}
            <Link
              href="/login"
              className="text-foreground underline-offset-4 transition-premium hover:underline"
            >
              Sign in to see all thirty.
            </Link>
          </p>
        </Reveal>
      </Section>

      {/* ─── Coverage ─────────────────────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <Reveal as="header" className="mb-12 max-w-[640px]">
          <p className="text-eyebrow">Beyond the dailies</p>
          <h2 className="mt-3 text-h1">Two more surfaces. Same discipline.</h2>
        </Reveal>
        <div className="grid gap-6 md:grid-cols-2">
          {COVERAGE.map((c, i) => (
            <Reveal key={c.title} delay={120 + i * 100}>
              <GlowCard className="h-full rounded-xl border border-border bg-card p-8 shadow-[var(--shadow-card)] transition-premium duration-300 hover:-translate-y-0.5">
                <p className="text-eyebrow">{c.eyebrow}</p>
                <h3 className="mt-4 text-h3">{c.title}</h3>
                <p className="mt-3 text-body text-muted-foreground">{c.body}</p>
              </GlowCard>
            </Reveal>
          ))}
        </div>
      </Section>

      {/* ─── Honesty ──────────────────────────────────────────── */}
      <Section spacing="default" width="narrow">
        <Reveal as="div" className="space-y-6">
          <p className="text-eyebrow">How we think about it</p>
          <h2 className="text-h1">
            Every claim traces back to a row in a database table.
          </h2>
          <p className="text-body-lg text-muted-foreground">
            Fortis doesn&rsquo;t generate opinions. It ranks, grades, and
            explains — and every numeric claim in every brief carries a
            verified pointer to the data behind it. When the system is
            uncertain, it says so. When the track record is thin, it shows
            you. That&rsquo;s the integrity.
          </p>
          <div className="pt-2">
            <Link href="/login" className={buttonVariants({ variant: "outline", size: "lg" })}>
              Sign in to the terminal
            </Link>
          </div>
        </Reveal>
      </Section>

      <SiteFooter />
    </main>
  )
}

const STATS = [
  { value: 3300, suffix: "+", label: "liquid US tickers swept" },
  { value: 30, suffix: "", label: "deep dossiers per scan" },
  { value: 100, suffix: "%", label: "claims source-traced" },
] as const

const FEATURES = [
  {
    index: "01",
    eyebrow: "Full-market sweep",
    title: "Pure math on everything.",
    body: "Every liquid US common stock with $1M+ in average daily dollar volume — price action, RSI, relative volume, 52-week change, breakout flags. Minutes, not hours.",
  },
  {
    index: "02",
    eyebrow: "Composite ranking",
    title: "Thirty names advance.",
    body: "A weighted composite advances the thirty names most worth a closer look this cycle. The expensive work doesn't start until a name earns it.",
  },
  {
    index: "03",
    eyebrow: "Deep analysis",
    title: "Graded conviction.",
    body: "Catalyst, insider posture, two-sided debate, critic verdict — synthesized into an A / B / C grade, every claim factcheck-verified against its source.",
  },
] as const

const COVERAGE = [
  {
    eyebrow: "Emerging watchlist",
    title: "Long-horizon asymmetric bets.",
    body: "A separate monthly engine hunts early-stage, under-discovered names with multibagger DNA — and treats “what kills it” as seriously as the 10x path. Speculative by design, labeled as such.",
  },
  {
    eyebrow: "Scan history",
    title: "Every run on the record.",
    body: "Each scan is archived in full — what it surfaced, what it scored, and when. Browse any past run; nothing is quietly rewritten after the fact.",
  },
] as const

const GRADE_VARIANT = { A: "gradeA", B: "gradeB", C: "gradeC" } as const

// Fallbacks for an empty database only — labeled "Illustrative data".
// Masked the same way as live rows so both modes tease identically.
const FALLBACK_PREVIEW: LandingPreviewRow[] = [
  { rank: 1, ticker: "NVDA", grade: "A", score: "94.2", price: "1,184.20", change: 2.41, masked: false },
  { rank: 2, ticker: null,   grade: "A", score: "91.8", price: null, change: null, masked: true },
  { rank: 3, ticker: null,   grade: "B", score: "88.4", price: null, change: null, masked: true },
  { rank: 4, ticker: null,   grade: "B", score: "86.1", price: null, change: null, masked: true },
  { rank: 5, ticker: null,   grade: "C", score: "81.7", price: null, change: null, masked: true },
]

const FALLBACK_TAPE: TapeItem[] = [
  { ticker: "NVDA", price: "1,184.20", change: 2.41 },
  { ticker: "AXON", price: "312.55", change: 1.07 },
  { ticker: "CRWD", price: "401.90", change: -0.62 },
  { ticker: "VRT",  price: "97.34",  change: 3.15 },
  { ticker: "PLTR", price: "24.61",  change: -1.84 },
  { ticker: "ANET", price: "289.45", change: 0.88 },
  { ticker: "SMCI", price: "612.08", change: -2.3 },
  { ticker: "MSFT", price: "415.27", change: 0.34 },
  { ticker: "LLY",  price: "771.90", change: 1.62 },
  { ticker: "AVGO", price: "1,322.46", change: -0.41 },
]
