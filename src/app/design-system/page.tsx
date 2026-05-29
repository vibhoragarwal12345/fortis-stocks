import Link from "next/link"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Section } from "@/components/ui/section"

export const metadata = { title: "Design system — Fortis" }
export const dynamic = "force-static"

// Preview surface for the Fortis design tokens. Lives outside any dashboard
// layout so the tokens shine without theming overrides. Delete (or gate
// behind a feature flag) before public launch.

const TYPE_SAMPLES = [
  {
    name: "Display",
    className: "text-display",
    spec: "60 / 1.05 / -0.02em / 600",
    sample: "Institutional research.",
  },
  {
    name: "H1",
    className: "text-h1",
    spec: "44 / 1.1 / -0.02em / 600",
    sample: "Built for wealth advisors.",
  },
  {
    name: "H2",
    className: "text-h2",
    spec: "32 / 1.2 / -0.01em / 600",
    sample: "Today's focus list",
  },
  {
    name: "H3",
    className: "text-h3",
    spec: "22 / 1.3 / -0.01em / 600",
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
] as const

const PALETTE_LIGHT = [
  { name: "Background",      hex: "#FBFBFD", swatch: "bg-background",          dark: false },
  { name: "Card",            hex: "#FFFFFF", swatch: "bg-card",                dark: false },
  { name: "Foreground",      hex: "#1D1D1F", swatch: "bg-foreground",          dark: true  },
  { name: "Muted",           hex: "#6E6E73", swatch: "bg-muted-foreground",    dark: true  },
  { name: "Tertiary",        hex: "#86868B", swatch: "bg-[#86868B]",           dark: true  },
  { name: "Secondary",       hex: "#F5F5F7", swatch: "bg-secondary",           dark: false },
  { name: "Border",          hex: "#E8E8ED", swatch: "bg-border",              dark: false },
  { name: "Accent (primary)",hex: "#1D1D1F", swatch: "bg-primary",             dark: true  },
] as const

const PALETTE_DARK = [
  { name: "Background",      hex: "#000000", swatch: "bg-black",              dark: true  },
  { name: "Card",            hex: "#1D1D1F", swatch: "bg-[#1D1D1F]",          dark: true  },
  { name: "Foreground",      hex: "#F5F5F7", swatch: "bg-[#F5F5F7]",          dark: false },
  { name: "Muted",           hex: "#86868B", swatch: "bg-[#86868B]",          dark: true  },
  { name: "Tertiary",        hex: "#6E6E73", swatch: "bg-[#6E6E73]",          dark: true  },
  { name: "Secondary",       hex: "#2C2C2E", swatch: "bg-[#2C2C2E]",          dark: true  },
  { name: "Border",          hex: "rgba(255,255,255,.10)", swatch: "bg-white/10", dark: false },
  { name: "Accent (primary)",hex: "#F5F5F7", swatch: "bg-[#F5F5F7]",          dark: false },
] as const

const SPACING_SCALE = [
  { token: "4",   px: 4   },
  { token: "8",   px: 8   },
  { token: "12",  px: 12  },
  { token: "16",  px: 16  },
  { token: "24",  px: 24  },
  { token: "32",  px: 32  },
  { token: "48",  px: 48  },
  { token: "64",  px: 64  },
  { token: "96",  px: 96  },
  { token: "128", px: 128 },
] as const

const RADII = [
  { name: "small",   px: 6,  class: "rounded-[6px]"  },
  { name: "button",  px: 8,  class: "rounded-md"     },
  { name: "card",    px: 12, class: "rounded-xl"     },
  { name: "card-lg", px: 16, class: "rounded-2xl"    },
] as const

export default function DesignSystemPage() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <Section spacing="hero" width="default">
        <div className="space-y-6">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Fortis · Design system
          </p>
          <h1 className="text-display">
            Restraint. <br />
            Typography does the heavy lifting.
          </h1>
          <p className="text-body-lg max-w-[640px] text-muted-foreground">
            One accent, soft surfaces, generous whitespace, and a single eased
            motion curve. The tokens below are the foundation we apply across
            every product surface.
          </p>
          <div className="flex flex-wrap gap-3">
            <Button size="lg">Primary action</Button>
            <Button size="lg" variant="outline">
              Secondary
            </Button>
            <Button size="lg" variant="ghost">
              Ghost
            </Button>
            <Link
              href="/dashboard"
              className="self-center text-small text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            >
              Back to product →
            </Link>
          </div>
        </div>
      </Section>

      {/* ── Typography ───────────────────────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <header className="mb-12">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Typography
          </p>
          <h2 className="mt-2 text-h1">A strict scale, two weights.</h2>
          <p className="mt-3 max-w-[640px] text-body text-muted-foreground">
            Inter via next/font, with the system stack as fallback (renders SF
            Pro on Apple devices). Headlines use negative letter-spacing for
            that signature crisp feel. Two weights — 400 and 600 — across the
            whole product.
          </p>
        </header>
        <ul className="space-y-8 border-t border-border pt-8">
          {TYPE_SAMPLES.map((t) => (
            <li
              key={t.name}
              className="grid gap-4 md:grid-cols-[180px_1fr] md:gap-10"
            >
              <div>
                <p className="text-small font-medium">{t.name}</p>
                <p className="mt-0.5 text-caption">{t.spec}</p>
              </div>
              <p className={t.className}>{t.sample}</p>
            </li>
          ))}
        </ul>
      </Section>

      {/* ── Color (Light) ────────────────────────────────────────── */}
      <Section spacing="default" width="default">
        <header className="mb-12">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Color · Light
          </p>
          <h2 className="mt-2 text-h1">One accent. Used sparingly.</h2>
          <p className="mt-3 max-w-[640px] text-body text-muted-foreground">
            Near-black accent on near-white surfaces. The accent appears only
            on primary CTAs, links, active states, and key highlights — never
            decoratively.
          </p>
        </header>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {PALETTE_LIGHT.map((c) => (
            <div key={c.name}>
              <div
                className={`h-24 w-full rounded-xl border border-border ${c.swatch}`}
              />
              <p className="mt-3 text-small font-medium">{c.name}</p>
              <p className="text-caption font-mono">{c.hex}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* ── Color (Dark) ─────────────────────────────────────────── */}
      <Section spacing="default" width="default">
        <header className="mb-12">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Color · Dark
          </p>
          <h2 className="mt-2 text-h1">Dark mode is a hallmark.</h2>
          <p className="mt-3 max-w-[640px] text-body text-muted-foreground">
            Near-black background, elevated surfaces slightly lighter. The
            accent inverts to off-white. Borders use white-alpha so they read
            subtly without crushing contrast.
          </p>
        </header>
        <div className="dark rounded-2xl bg-background p-10 text-foreground">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {PALETTE_DARK.map((c) => (
              <div key={c.name}>
                <div
                  className={`h-24 w-full rounded-xl border border-white/10 ${c.swatch}`}
                />
                <p className="mt-3 text-small font-medium text-foreground">
                  {c.name}
                </p>
                <p className="text-caption font-mono text-muted-foreground">
                  {c.hex}
                </p>
              </div>
            ))}
          </div>
        </div>
      </Section>

      {/* ── Buttons ──────────────────────────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <header className="mb-12">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Buttons
          </p>
          <h2 className="mt-2 text-h1">Four variants. Five sizes.</h2>
          <p className="mt-3 max-w-[640px] text-body text-muted-foreground">
            All transitions ride a single curve —{" "}
            <code className="font-mono text-small">
              cubic-bezier(0.16, 1, 0.3, 1)
            </code>{" "}
            — at 200ms. Active state nudges 1px instead of harsh box-shadows.
          </p>
        </header>

        <div className="space-y-10">
          {(["default", "outline", "ghost", "secondary", "destructive"] as const).map((variant) => (
            <div key={variant}>
              <p className="mb-3 text-caption uppercase tracking-[0.14em]">
                {variant}
              </p>
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
            <p className="mb-3 text-caption uppercase tracking-[0.14em]">link</p>
            <div className="flex flex-wrap items-center gap-6 text-body">
              <Button variant="link">Inline link</Button>
              <Button variant="link" disabled>
                Disabled link
              </Button>
            </div>
          </div>
        </div>
      </Section>

      {/* ── Cards ────────────────────────────────────────────────── */}
      <Section spacing="default" width="default">
        <header className="mb-12">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Cards
          </p>
          <h2 className="mt-2 text-h1">Borders, not shadows.</h2>
          <p className="mt-3 max-w-[640px] text-body text-muted-foreground">
            Three tones — bordered for the everyday card, elevated for hero /
            feature moments, flat when the surrounding surface already defines
            the boundary.
          </p>
        </header>

        <div className="grid gap-6 md:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle>Default</CardTitle>
              <CardDescription>1px border, no shadow</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-small text-muted-foreground">
                The everyday card. Used for every list row, metric tile, and
                form section across the product.
              </p>
            </CardContent>
            <CardFooter>
              <Button variant="ghost" size="sm">
                Open report →
              </Button>
            </CardFooter>
          </Card>

          <Card tone="elevated">
            <CardHeader>
              <CardTitle>Elevated</CardTitle>
              <CardDescription>Soft diffuse shadow</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-small text-muted-foreground">
                Hero or feature moments. Shadow is{" "}
                <code className="font-mono">
                  0 4px 24px rgba(0,0,0,0.06)
                </code>
                .
              </p>
            </CardContent>
          </Card>

          <Card tone="flat" className="bg-secondary">
            <CardHeader>
              <CardTitle>Flat</CardTitle>
              <CardDescription>Sits on a surface that defines it</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-small text-muted-foreground">
                When the surrounding container already has a border or color,
                the card stays plain. Less is more.
              </p>
            </CardContent>
          </Card>
        </div>
      </Section>

      {/* ── Spacing & radius ─────────────────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <header className="mb-12">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Spacing &amp; radius
          </p>
          <h2 className="mt-2 text-h1">A doubled scale.</h2>
          <p className="mt-3 max-w-[640px] text-body text-muted-foreground">
            Spacing roughly doubles at each step: 4 / 8 / 12 / 16 / 24 / 32 /
            48 / 64 / 96 / 128. Sections breathe at 96-128px vertical, content
            tops out at 1120px wide.
          </p>
        </header>

        <div className="grid gap-12 md:grid-cols-2">
          <div>
            <p className="text-h3 mb-6">Spacing</p>
            <ul className="space-y-3">
              {SPACING_SCALE.map((s) => (
                <li key={s.token} className="flex items-center gap-4">
                  <span className="w-10 text-caption font-mono">{s.token}</span>
                  <span
                    className="h-3 rounded-sm bg-foreground"
                    style={{ width: `${s.px}px` }}
                  />
                  <span className="text-caption font-mono">{s.px}px</span>
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
                    className={`size-16 border border-border bg-card ${r.class}`}
                  />
                  <div>
                    <p className="text-small font-medium">{r.name}</p>
                    <p className="text-caption font-mono">{r.px}px</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Section>

      {/* ── Motion ───────────────────────────────────────────────── */}
      <Section spacing="default" width="default">
        <header className="mb-12">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Motion
          </p>
          <h2 className="mt-2 text-h1">Subtle. Fast. Eased.</h2>
          <p className="mt-3 max-w-[640px] text-body text-muted-foreground">
            All interactive transitions ride{" "}
            <code className="font-mono text-small">cubic-bezier(0.16, 1, 0.3, 1)</code>{" "}
            at 200ms (small) or 300–400ms (larger). Hover the swatch — tone
            and lift transition together.
          </p>
        </header>

        <div className="grid gap-6 md:grid-cols-3">
          <div className="group cursor-pointer rounded-xl border border-border bg-card p-8 transition-premium hover:-translate-y-0.5 hover:bg-secondary">
            <p className="text-h3">Hover</p>
            <p className="mt-2 text-small text-muted-foreground">
              200ms fast. Background + 2px lift.
            </p>
          </div>
          <div className="group cursor-pointer rounded-xl border border-border bg-card p-8 transition-premium duration-300 hover:-translate-y-1 hover:shadow-[var(--shadow-md)]">
            <p className="text-h3">Card lift</p>
            <p className="mt-2 text-small text-muted-foreground">
              300ms base. Shadow blooms on hover.
            </p>
          </div>
          <div className="group cursor-pointer rounded-xl border border-border bg-card p-8 transition-premium duration-[400ms] hover:scale-[1.02]">
            <p className="text-h3">Scale 1.02</p>
            <p className="mt-2 text-small text-muted-foreground">
              400ms slow. Max 2% to stay restrained.
            </p>
          </div>
        </div>
      </Section>

      <Section spacing="compact" width="default">
        <p className="text-caption">
          End of design system preview. This page is gated behind /design-system
          and can be removed when the system is locked.
        </p>
      </Section>
    </main>
  )
}
