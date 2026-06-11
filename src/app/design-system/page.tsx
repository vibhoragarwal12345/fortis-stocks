import Link from "next/link"
import { redirect } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { AuroraField } from "@/components/ui/aurora-field"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import { SiteFooter } from "@/components/site-footer"
import { GlowCard } from "@/components/ui/glow-card"
import { TickerTape, type TapeItem } from "@/components/ui/ticker-tape"
import { WordReveal } from "@/components/ui/word-reveal"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { CountUp } from "@/components/ui/count-up"
import { CursorGlow } from "@/components/ui/cursor-glow"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Reveal } from "@/components/ui/reveal"
import { Section } from "@/components/ui/section"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export const metadata = { title: "Design system — Fortis" }
export const dynamic = "force-dynamic"

// Internal preview surface for the "Institutional Terminal" design system.
// Lives outside the dashboard layout so the raw tokens shine without tenant
// theming. Auth-gated at launch: signup is invite-only, so this is
// team-visible without being public.

const TYPE_SAMPLES = [
  {
    name: "Display",
    className: "text-display",
    spec: "64 / 1.03 / -0.035em / 600",
    sample: "Signal, not noise.",
  },
  {
    name: "H1",
    className: "text-h1",
    spec: "44 / 1.08 / -0.025em / 600",
    sample: "The full market, twice a day.",
  },
  {
    name: "H2",
    className: "text-h2",
    spec: "32 / 1.15 / -0.02em / 600",
    sample: "Today's focus list",
  },
  {
    name: "H3",
    className: "text-h3",
    spec: "21 / 1.3 / -0.012em / 600",
    sample: "Pre-market brief",
  },
  {
    name: "Body large",
    className: "text-body-lg",
    spec: "19 / 1.6 / 400",
    sample:
      "Fortis surfaces the picks the desk should actually act on — every claim traced to its underlying data.",
  },
  {
    name: "Body",
    className: "text-body",
    spec: "16 / 1.6 / 400",
    sample:
      "Default paragraph copy. Designed to be read without effort across long-form research notes.",
  },
  {
    name: "Small",
    className: "text-small",
    spec: "14 / 1.5 / 400",
    sample:
      "Used for compact metadata and dense table rows where 16px would feel heavy.",
  },
  {
    name: "Caption",
    className: "text-caption",
    spec: "13 / 1.4 / 400, muted",
    sample: "Generated 12 minutes ago · verified 96%",
  },
  {
    name: "Eyebrow",
    className: "text-eyebrow",
    spec: "11 / 0.16em / 500, uppercase",
    sample: "Market scan · Pre-market",
  },
  {
    name: "Data",
    className: "text-data text-body",
    spec: "Geist Mono / tabular-nums",
    sample: "NVDA  1,184.20  +2.41%  94.2",
  },
] as const

// Swatches use the live tokens, so each palette block renders truthfully in
// its own mode wrapper. Hexes shown are the canonical values in globals.css.
const PALETTE_DARK = [
  { name: "Background · base",   hex: "#0A0C10", swatch: "bg-background" },
  { name: "Card · raised",       hex: "#12151C", swatch: "bg-card" },
  { name: "Popover · overlay",   hex: "#181C25", swatch: "bg-popover" },
  { name: "Secondary",           hex: "#1C212B", swatch: "bg-secondary" },
  { name: "Foreground",          hex: "#ECEFF4", swatch: "bg-foreground" },
  { name: "Muted foreground",    hex: "#8B95A7", swatch: "bg-muted-foreground" },
  { name: "Border",              hex: "white α .08", swatch: "bg-white/10" },
  { name: "Highlight",           hex: "#2DD4ED", swatch: "bg-highlight" },
] as const

const PALETTE_LIGHT = [
  { name: "Background",          hex: "#F6F7F9", swatch: "bg-background" },
  { name: "Card",                hex: "#FFFFFF", swatch: "bg-card" },
  { name: "Foreground",          hex: "#0F1217", swatch: "bg-foreground" },
  { name: "Muted foreground",    hex: "#5D6877", swatch: "bg-muted-foreground" },
  { name: "Secondary",           hex: "#EDEFF3", swatch: "bg-secondary" },
  { name: "Border",              hex: "#E3E6EB", swatch: "bg-border" },
  { name: "Highlight",           hex: "#087E9D", swatch: "bg-highlight" },
  { name: "Primary",             hex: "#11141A", swatch: "bg-primary" },
] as const

const SEMANTIC = [
  {
    name: "Highlight",
    var: "--highlight",
    className: "bg-highlight",
    text: "text-highlight",
    rule: "The one accent. Grades, focus rings, live-data markers. Never decoration.",
  },
  {
    name: "Gain",
    var: "--gain",
    className: "bg-gain",
    text: "text-gain",
    rule: "Positive returns and deltas only — deliberately distinct from the accent.",
  },
  {
    name: "Loss",
    var: "--loss",
    className: "bg-loss",
    text: "text-loss",
    rule: "Negative returns, drawdowns, destructive states.",
  },
  {
    name: "Warning",
    var: "--warning",
    className: "bg-warning",
    text: "text-warning",
    rule: "Caution tier — B grades, stale data, soft alerts.",
  },
] as const

const FOCUS_ROWS = [
  { rank: 1, ticker: "NVDA", company: "NVIDIA Corp",        grade: "A" as const, score: "94.2", price: "1,184.20", change: +2.41 },
  { rank: 2, ticker: "AXON", company: "Axon Enterprise",    grade: "A" as const, score: "91.8", price: "312.55",   change: +1.07 },
  { rank: 3, ticker: "CRWD", company: "CrowdStrike",        grade: "B" as const, score: "88.4", price: "401.90",   change: -0.62 },
  { rank: 4, ticker: "VRT",  company: "Vertiv Holdings",    grade: "B" as const, score: "86.1", price: "97.34",    change: +3.15 },
  { rank: 5, ticker: "PLTR", company: "Palantir",           grade: "C" as const, score: "81.7", price: "24.61",    change: -1.84 },
] as const

const SPACING_SCALE = [
  { token: "4",   px: 4 },
  { token: "8",   px: 8 },
  { token: "12",  px: 12 },
  { token: "16",  px: 16 },
  { token: "24",  px: 24 },
  { token: "32",  px: 32 },
  { token: "48",  px: 48 },
  { token: "64",  px: 64 },
  { token: "96",  px: 96 },
  { token: "128", px: 128 },
] as const

const RADII = [
  { name: "small",   px: 6,  class: "rounded-[6px]" },
  { name: "button",  px: 8,  class: "rounded-md" },
  { name: "card",    px: 12, class: "rounded-xl" },
  { name: "card-lg", px: 16, class: "rounded-2xl" },
] as const

const GRADE_VARIANT = { A: "gradeA", B: "gradeB", C: "gradeC" } as const

const TAPE_ITEMS: TapeItem[] = [
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

function SectionHeader({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string
  title: string
  children?: React.ReactNode
}) {
  return (
    <header className="mb-12">
      <p className="text-eyebrow">{eyebrow}</p>
      <h2 className="mt-3 text-h1">{title}</h2>
      {children ? (
        <p className="mt-3 max-w-[640px] text-body text-muted-foreground">
          {children}
        </p>
      ) : null}
    </header>
  )
}

export default async function DesignSystemPage() {
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) redirect("/login")

  return (
    <main className="relative min-h-screen bg-background bg-ambient text-foreground">
      <CursorGlow />

      {/* ── Top bar — same mark as landing/auth/admin ────────────── */}
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
            <span className="text-eyebrow">Design system</span>
          </Link>
          <Link
            href="/dashboard"
            className={buttonVariants({ variant: "ghost", size: "sm" })}
          >
            Back to product
          </Link>
        </div>
      </header>

      {/* ── Hero ─────────────────────────────────────────────────── */}
      <div className="relative overflow-hidden">
        <AuroraField />
        <Section spacing="hero" width="default" className="relative">
          <div className="space-y-6">
            <Reveal>
              <p className="text-eyebrow text-highlight">
                Fortis · Institutional Terminal
              </p>
            </Reveal>
            <WordReveal
              as="h1"
              className="text-display max-w-[760px]"
              text="Futuristic through restraint, not noise."
              delay={120}
              step={90}
            />
            <Reveal delay={320}>
              <p className="text-body-lg max-w-[640px] text-muted-foreground">
                Dark-first layered surfaces, one electric-cyan accent, Geist
                with tight tracking, mono numerals for every metric, and a
                single eased motion curve. These tokens are the foundation for
                every product surface.
              </p>
            </Reveal>
            <Reveal delay={420}>
              <div className="flex flex-wrap items-center gap-3">
                <Button size="lg">Primary action</Button>
                <Button size="lg" variant="outline">Secondary</Button>
                <Button size="lg" variant="ghost">Ghost</Button>
              </div>
            </Reveal>
          </div>

          {/* Stat band — CountUp demo doubling as the product's true numbers. */}
          <Reveal delay={520}>
            <div className="mt-16 grid max-w-[640px] grid-cols-3 gap-px overflow-hidden rounded-xl border border-border bg-border backdrop-blur-sm">
              {(
                [
                  { value: 3312, label: "tickers scanned", suffix: "" },
                  { value: 30, label: "deep-analyzed", suffix: "" },
                  { value: 96, label: "factcheck pass %", suffix: "%" },
                ] as const
              ).map((s) => (
                <div key={s.label} className="bg-card/80 px-6 py-5">
                  <p className="text-data text-h2 text-foreground">
                    <CountUp value={s.value} suffix={s.suffix} />
                  </p>
                  <p className="mt-1 text-caption">{s.label}</p>
                </div>
              ))}
            </div>
          </Reveal>
        </Section>

        {/* Live tape — pauses on hover, edge-faded, seam-free. */}
        <Reveal delay={620}>
          <TickerTape items={TAPE_ITEMS} className="relative" />
        </Reveal>
      </div>

      {/* ── Typography ───────────────────────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <SectionHeader eyebrow="Typography" title="A strict scale. Geist everywhere.">
          Geist Sans carries the whole scale with negative tracking on display
          sizes; Geist Mono with tabular numerals renders every metric, price,
          and ticker. Two weights — 400 and 600 — across the entire product.
        </SectionHeader>
        <ul className="space-y-8 border-t border-border pt-8">
          {TYPE_SAMPLES.map((t) => (
            <li
              key={t.name}
              className="grid gap-4 md:grid-cols-[180px_1fr] md:gap-10"
            >
              <div>
                <p className="text-small font-medium">{t.name}</p>
                <p className="mt-0.5 text-caption text-data">{t.spec}</p>
              </div>
              <p className={t.className}>{t.sample}</p>
            </li>
          ))}
        </ul>
      </Section>

      {/* ── Surfaces & elevation (dark) ──────────────────────────── */}
      <Section spacing="default" width="default">
        <SectionHeader eyebrow="Color · Dark" title="Three layers of depth.">
          Dark mode is the hallmark: deep blue-black base, raised cards, and
          overlay surfaces. Raised cards catch a 1px top light edge instead of
          heavy borders. Borders are white-alpha so they whisper.
        </SectionHeader>

        {/* Live elevation demo: base → raised → overlay. */}
        <div className="mb-12 rounded-2xl border border-border bg-background p-8 md:p-10">
          <p className="text-eyebrow mb-6">Base · #0A0C10</p>
          <div className="rounded-xl bg-card p-6 shadow-[var(--shadow-card)] md:p-8">
            <p className="text-eyebrow mb-6">Raised · #12151C · 1px light-catch</p>
            <div className="rounded-lg bg-popover p-6 shadow-[var(--shadow-md)]">
              <p className="text-eyebrow">Overlay · #181C25 · diffuse bloom</p>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {PALETTE_DARK.map((c) => (
            <div key={c.name}>
              <div
                className={`h-24 w-full rounded-xl border border-border ${c.swatch}`}
              />
              <p className="mt-3 text-small font-medium">{c.name}</p>
              <p className="text-caption text-data">{c.hex}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* ── Atmosphere ───────────────────────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <SectionHeader eyebrow="Atmosphere" title="Light that earns its place.">
          Dark heroes carry an aurora field — three blurred light pools
          drifting on offset 26–38s curves over a hairline blueprint grid,
          finished with film grain so nothing reads as a flat digital
          gradient. Compositor-only CSS: no canvas, no rAF. Drift freezes on
          mobile and collapses under reduced motion.
        </SectionHeader>

        <div className="relative h-[340px] overflow-hidden rounded-2xl border border-border bg-background">
          <AuroraField />
          <div className="relative flex h-full flex-col items-start justify-end p-8 md:p-10">
            <p className="text-eyebrow text-highlight">Aurora field</p>
            <p className="mt-2 max-w-[480px] text-h3">
              Value props stay legible — the light never exceeds 22% tint.
            </p>
            <p className="mt-2 text-caption">
              cyan · indigo · sea-green / blur 90px / grain 5%
            </p>
          </div>
        </div>
      </Section>

      {/* ── Light mode island ────────────────────────────────────── */}
      <Section spacing="default" width="default">
        <SectionHeader eyebrow="Color · Light" title="The refined daytime variant.">
          Same tokens, re-resolved. The accent deepens to a sea-glass teal that
          holds AA contrast on white; gain and loss shift darker for print-like
          clarity. Everything below renders inside a literal light island.
        </SectionHeader>

        <div className="light rounded-2xl border border-border bg-background p-8 text-foreground md:p-10">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {PALETTE_LIGHT.map((c) => (
              <div key={c.name}>
                <div
                  className={`h-24 w-full rounded-xl border border-border ${c.swatch}`}
                />
                <p className="mt-3 text-small font-medium">{c.name}</p>
                <p className="text-caption text-data">{c.hex}</p>
              </div>
            ))}
          </div>

          <div className="mt-10 grid gap-6 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Light-mode card</CardTitle>
                <CardDescription>
                  Soft #F6F7F9 page, white cards, hairline borders.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                <Badge variant="gradeA">Grade A</Badge>
                <Badge variant="success">+12.4%</Badge>
                <Badge variant="destructive">−3.1%</Badge>
                <Badge variant="neutral">15-min delayed</Badge>
              </CardContent>
              <CardFooter>
                <Button size="sm">Primary</Button>
                <Button size="sm" variant="outline" className="ml-2">
                  Secondary
                </Button>
              </CardFooter>
            </Card>
            <div className="flex flex-col justify-center gap-3">
              <Label htmlFor="ds-light-email">Work email</Label>
              <Input
                id="ds-light-email"
                type="email"
                placeholder="advisor@fortis.agency"
              />
              <p className="text-caption">
                Focus ring resolves to the light-mode teal automatically.
              </p>
            </div>
          </div>
        </div>
      </Section>

      {/* ── Accent & semantic data colors ────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <SectionHeader eyebrow="Semantics" title="One accent. Three data colors.">
          Electric cyan marks signal — it never decorates. Gains and losses get
          their own hues so a conviction grade can never be misread as a
          return. Each carries a usage rule, not just a hex.
        </SectionHeader>
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {SEMANTIC.map((s) => (
            <div
              key={s.name}
              className="rounded-xl border border-border bg-card p-6 shadow-[var(--shadow-card)]"
            >
              <div className={`h-10 w-10 rounded-md ${s.className}`} />
              <p className={`mt-4 text-h3 ${s.text}`}>{s.name}</p>
              <p className="mt-1 text-caption text-data">{s.var}</p>
              <p className="mt-3 text-small text-muted-foreground">{s.rule}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* ── Buttons ──────────────────────────────────────────────── */}
      <Section spacing="default" width="default">
        <SectionHeader eyebrow="Buttons" title="Six variants. Restrained.">
          All transitions ride cubic-bezier(0.16, 1, 0.3, 1) at 200ms. Active
          state nudges 1px down — no harsh shadows, no color flashes.
        </SectionHeader>

        <div className="space-y-10">
          {(
            ["default", "outline", "ghost", "secondary", "destructive"] as const
          ).map((variant) => (
            <div key={variant}>
              <p className="text-eyebrow mb-3">{variant}</p>
              <div className="flex flex-wrap items-center gap-3">
                <Button variant={variant} size="sm">Small</Button>
                <Button variant={variant} size="default">Default</Button>
                <Button variant={variant} size="lg">Large</Button>
                <Button variant={variant} size="xl">Hero CTA</Button>
                <Button variant={variant} size="default" disabled>
                  Disabled
                </Button>
              </div>
            </div>
          ))}

          <div>
            <p className="text-eyebrow mb-3">link</p>
            <div className="flex flex-wrap items-center gap-6 text-body">
              <Button variant="link">Inline link</Button>
              <Button variant="link" disabled>Disabled link</Button>
            </div>
          </div>
        </div>
      </Section>

      {/* ── Badges & grades ──────────────────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <SectionHeader eyebrow="Badges" title="Grades read at a glance.">
          Conviction grades are pills on the semantic scale — cyan for A, amber
          for B, muted for C. Returns use gain/loss. Informational chips stay
          neutral so they never compete with signal.
        </SectionHeader>
        <div className="space-y-8">
          <div>
            <p className="text-eyebrow mb-3">Conviction grades</p>
            <div className="flex flex-wrap items-center gap-3">
              <Badge variant="gradeA">A · High conviction</Badge>
              <Badge variant="gradeB">B · Watch</Badge>
              <Badge variant="gradeC">C · Monitor</Badge>
            </div>
          </div>
          <div>
            <p className="text-eyebrow mb-3">Data semantics</p>
            <div className="flex flex-wrap items-center gap-3">
              <Badge variant="success">+8.4% since pick</Badge>
              <Badge variant="destructive">−2.7% drawdown</Badge>
              <Badge variant="warning">Earnings in 3d</Badge>
              <Badge variant="accent">Live scan</Badge>
            </div>
          </div>
          <div>
            <p className="text-eyebrow mb-3">Neutral chrome</p>
            <div className="flex flex-wrap items-center gap-3">
              <Badge variant="default">Default</Badge>
              <Badge variant="secondary">Secondary</Badge>
              <Badge variant="outline">Outline</Badge>
              <Badge variant="neutral">15-min delayed</Badge>
            </div>
          </div>
        </div>
      </Section>

      {/* ── Data table ───────────────────────────────────────────── */}
      <Section spacing="default" width="default">
        <SectionHeader eyebrow="Data table" title="The core surface.">
          56px rows, hairline separators, uppercase micro-headers, mono
          numerals right-aligned. Hover tints the row; the whole row is the
          click target. No zebra stripes, no vertical rules, no clutter.
        </SectionHeader>

        <Card className="py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12 pl-6">#</TableHead>
                <TableHead>Ticker</TableHead>
                <TableHead>Grade</TableHead>
                <TableHead numeric>Score</TableHead>
                <TableHead numeric>Price</TableHead>
                <TableHead numeric className="pr-6">1D</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {FOCUS_ROWS.map((r) => (
                <TableRow key={r.ticker} interactive>
                  <TableCell className="pl-6 text-data text-muted-foreground">
                    {r.rank}
                  </TableCell>
                  <TableCell>
                    <span className="text-data font-medium">{r.ticker}</span>
                    <span className="ml-3 hidden text-small text-muted-foreground sm:inline">
                      {r.company}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge variant={GRADE_VARIANT[r.grade]}>{r.grade}</Badge>
                  </TableCell>
                  <TableCell numeric>{r.score}</TableCell>
                  <TableCell numeric>{r.price}</TableCell>
                  <TableCell
                    numeric
                    className={`pr-6 ${r.change >= 0 ? "text-gain" : "text-loss"}`}
                  >
                    {r.change >= 0 ? "+" : ""}
                    {r.change.toFixed(2)}%
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      </Section>

      {/* ── Forms ────────────────────────────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <SectionHeader eyebrow="Forms" title="Effortless and trustworthy.">
          44px inputs matched to button height, transparent backgrounds that
          inherit the card, and a soft cyan focus ring — the only moment the
          accent appears in a form.
        </SectionHeader>
        <div className="grid max-w-[760px] gap-8 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="ds-email">Email</Label>
            <Input id="ds-email" type="email" placeholder="advisor@fortis.agency" />
            <p className="text-caption">Click in to see the focus treatment.</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ds-disabled">Disabled</Label>
            <Input id="ds-disabled" disabled placeholder="Locked to invite" />
            <p className="text-caption">40% opacity, pointer disabled.</p>
          </div>
        </div>
      </Section>

      {/* ── Loading states ───────────────────────────────────────── */}
      <Section spacing="default" width="default">
        <SectionHeader eyebrow="Loading" title="Shimmer, never spinners.">
          Skeletons sweep a slow light gradient across the surface and mirror
          the final layout exactly, so content lands without shift. Reduced
          motion degrades them to static tinted blocks.
        </SectionHeader>
        <Card className="max-w-[640px] py-0">
          <div className="space-y-4 p-6">
            <div className="flex items-center justify-between">
              <Skeleton className="h-5 w-32" />
              <Skeleton className="h-5 w-14 rounded-full" />
            </div>
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-4/5" />
            <div className="flex gap-3 pt-2">
              <Skeleton className="h-9 w-28 rounded-md" />
              <Skeleton className="h-9 w-28 rounded-md" />
            </div>
          </div>
        </Card>
      </Section>

      {/* ── Motion ───────────────────────────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <SectionHeader eyebrow="Motion" title="One curve. Earned moments.">
          Everything eases on cubic-bezier(0.16, 1, 0.3, 1) — 200ms for
          micro-interactions, up to 500ms for reveals. Content fades up as it
          enters the viewport (this page demonstrates it), numbers count up
          once, and all of it collapses under prefers-reduced-motion.
        </SectionHeader>

        <div className="grid gap-6 md:grid-cols-3">
          <Reveal>
            <GlowCard className="group cursor-pointer rounded-xl border border-border bg-card p-8 shadow-[var(--shadow-card)] transition-premium hover:-translate-y-0.5">
              <p className="text-h3">Spotlight</p>
              <p className="mt-2 text-small text-muted-foreground">
                A light pool and a lit border segment track the cursor. Move
                across this card.
              </p>
            </GlowCard>
          </Reveal>
          <Reveal delay={80}>
            <GlowCard className="group cursor-pointer rounded-xl border border-border bg-card p-8 shadow-[var(--shadow-card)] transition-premium duration-300 hover:-translate-y-1 hover:shadow-[var(--shadow-md)]">
              <p className="text-h3">Card lift</p>
              <p className="mt-2 text-small text-muted-foreground">
                300ms. Shadow blooms as the card rises — spotlight rides
                along.
              </p>
            </GlowCard>
          </Reveal>
          <Reveal delay={160}>
            <GlowCard className="group cursor-pointer rounded-xl border border-border bg-card p-8 shadow-[var(--shadow-card)] transition-premium duration-[400ms] hover:scale-[1.02]">
              <p className="text-h3">Scale 1.02</p>
              <p className="mt-2 text-small text-muted-foreground">
                400ms. Max 2% — restraint is the brand.
              </p>
            </GlowCard>
          </Reveal>
        </div>

        <div className="mt-10 rounded-xl border border-border bg-card p-6 shadow-[var(--shadow-card)]">
          <p className="text-eyebrow mb-3">Headline reveal</p>
          <WordReveal
            as="p"
            className="text-h2"
            text="Every word rises out of its own mask."
            step={80}
          />
          <p className="mt-3 text-caption">
            Pure CSS, server-rendered, staggered 70–90ms per word. Reload the
            page to replay the hero.
          </p>
        </div>

        <div className="mt-10 grid gap-6 md:grid-cols-2">
          <div className="rounded-xl border border-border bg-card p-6 shadow-[var(--shadow-card)]">
            <p className="text-eyebrow mb-2">Count-up</p>
            <p className="text-data text-h1">
              <CountUp value={94.2} decimals={1} />
              <span className="text-muted-foreground"> / 100 composite</span>
            </p>
            <p className="mt-2 text-caption">
              Fires once on first viewport entry. Reduced motion renders the
              final value immediately.
            </p>
          </div>

          {/* Kinetics — the table entrance language: rows cascade in
              30ms apart, score bars sweep open from zero. */}
          <div className="rounded-xl border border-border bg-card p-6 shadow-[var(--shadow-card)]">
            <p className="text-eyebrow mb-4">Kinetics · cascade + bar sweep</p>
            <ul className="space-y-3">
              {FOCUS_ROWS.slice(0, 4).map((r, i) => (
                <li
                  key={r.ticker}
                  className="row-in flex items-center justify-between gap-4"
                  style={{ animationDelay: `${i * 90}ms` }}
                >
                  <span className="text-data text-small font-medium">
                    {r.ticker}
                  </span>
                  <span className="flex items-center gap-3">
                    <span
                      aria-hidden
                      className="block h-0.5 w-24 overflow-hidden rounded-full bg-secondary"
                    >
                      <span
                        className="bar-grow block h-full rounded-full bg-highlight/80"
                        style={{
                          width: `${Number(r.score)}%`,
                          animationDelay: `${i * 90 + 250}ms`,
                        }}
                      />
                    </span>
                    <span className="text-data text-small">{r.score}</span>
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-4 text-caption">
              Reload to replay. Used by the shortlist table and grade
              distribution bars.
            </p>
          </div>
        </div>
      </Section>

      {/* ── Spacing & radius ─────────────────────────────────────── */}
      <Section spacing="default" width="default">
        <SectionHeader eyebrow="Spacing & radius" title="A doubled scale.">
          Spacing roughly doubles at each step. Sections breathe at 96–128px
          vertical; content tops out at 1120px (1280px for wide data tables).
          Radii: 6 small, 8 buttons, 12–16 cards.
        </SectionHeader>

        <div className="grid gap-12 md:grid-cols-2">
          <div>
            <p className="text-h3 mb-6">Spacing</p>
            <ul className="space-y-3">
              {SPACING_SCALE.map((s) => (
                <li key={s.token} className="flex items-center gap-4">
                  <span className="w-10 text-caption text-data">{s.token}</span>
                  <span
                    className="h-3 rounded-sm bg-highlight/70"
                    style={{ width: `${s.px}px` }}
                  />
                  <span className="text-caption text-data">{s.px}px</span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <p className="text-h3 mb-6">Radius</p>
            <ul className="space-y-4">
              {RADII.map((r) => (
                <li key={r.name} className="flex items-center gap-6">
                  <div
                    className={`size-16 border border-border bg-card shadow-[var(--shadow-card)] ${r.class}`}
                  />
                  <div>
                    <p className="text-small font-medium">{r.name}</p>
                    <p className="text-caption text-data">{r.px}px</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Section>

      <Section spacing="compact" width="default">
        <p className="text-caption">
          End of design-system preview. Tokens live in src/app/globals.css;
          primitives in src/components/ui. Internal reference — gate or
          remove before public launch.
        </p>
      </Section>

      <SiteFooter />
    </main>
  )
}
