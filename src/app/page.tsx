import Link from "next/link"

import { Button } from "@/components/ui/button"
import { Reveal } from "@/components/ui/reveal"
import { Section } from "@/components/ui/section"

export const metadata = {
  title: "Fortis Stock Intelligence",
  description:
    "Institutional research for wealth advisors. Conviction-graded daily briefs, focus lists, and long-horizon watchlists — every claim traced to its source.",
}

// Gateway. Not a marketing page. Three restrained sections, one confident
// CTA, and the access-is-invite-only line. Scroll reveals do the rest.

export default function Home() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      {/* ─── Hero ─────────────────────────────────────────────── */}
      <Section spacing="hero" width="default">
        <Reveal as="div" className="max-w-[860px] space-y-8">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            Fortis · Stock Intelligence
          </p>
          <h1 className="text-display">
            Institutional research,
            <br />
            distilled for the desk.
          </h1>
          <p className="text-body-lg max-w-[640px] text-muted-foreground">
            Conviction-graded daily briefs, focus lists, and long-horizon
            watchlists — every claim traced to its underlying data. Built for
            wealth advisors who&rsquo;d rather act than scroll.
          </p>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3 pt-2">
            <Link
              href="/login"
              className="inline-flex h-11 items-center justify-center rounded-md bg-primary px-6 text-[15px] font-medium text-primary-foreground transition-premium hover:bg-primary/90 active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              Sign in
            </Link>
            <span className="text-small text-muted-foreground">
              Access is currently invite-only.
            </span>
          </div>
        </Reveal>
      </Section>

      {/* ─── What you get ─────────────────────────────────────── */}
      <Section spacing="default" width="default" surface>
        <Reveal as="header" className="mb-14 max-w-[640px]">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            What you get
          </p>
          <h2 className="mt-2 text-h1">
            Three surfaces. <br className="hidden md:inline" />
            Each one earns its place.
          </h2>
        </Reveal>

        <ul className="grid gap-6 md:grid-cols-3">
          {FEATURES.map((f, i) => (
            <Reveal
              key={f.title}
              as="li"
              delay={120 + i * 100}
              className="rounded-xl border border-border bg-card p-8 transition-premium duration-300 hover:-translate-y-0.5"
            >
              <p className="text-caption uppercase tracking-[0.16em] text-muted-foreground">
                {f.eyebrow}
              </p>
              <h3 className="mt-3 text-h3">{f.title}</h3>
              <p className="mt-3 text-body text-muted-foreground">
                {f.body}
              </p>
            </Reveal>
          ))}
        </ul>
      </Section>

      {/* ─── Honesty ──────────────────────────────────────────── */}
      <Section spacing="default" width="narrow">
        <Reveal as="div" className="space-y-6">
          <p className="text-caption uppercase tracking-[0.18em] text-muted-foreground">
            How we think about it
          </p>
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
        </Reveal>
      </Section>

      {/* ─── Footer ───────────────────────────────────────────── */}
      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-[1120px] flex-col gap-3 px-6 py-10 md:flex-row md:items-center md:justify-between md:px-10">
          <p className="text-caption">
            &copy; {new Date().getFullYear()} Fortis Stock Intelligence. For
            licensed advisor review — not investment advice.
          </p>
          <p className="text-caption">
            <Link
              href="/login"
              className="underline-offset-4 hover:text-foreground hover:underline"
            >
              Sign in
            </Link>
          </p>
        </div>
      </footer>
    </main>
  )
}

const FEATURES = [
  {
    eyebrow: "Daily",
    title: "Conviction-graded briefs.",
    body:
      "Pre-market, midday, and close briefs. Every pick carries an A / B / C / D grade with the quant signals that earned it.",
  },
  {
    eyebrow: "Weekly",
    title: "Focus list with thesis.",
    body:
      "The ranked names with bull case, bear case, catalyst category, position guidance, and the insider posture behind them.",
  },
  {
    eyebrow: "Long-horizon",
    title: "Emerging watchlist.",
    body:
      "Small-cap multibagger candidates tracked over years. Honest tier framing — most will fail, the winners can return many multiples.",
  },
] as const
