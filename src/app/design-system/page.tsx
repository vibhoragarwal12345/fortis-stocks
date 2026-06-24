import type { CSSProperties } from "react"
import { DM_Serif_Display, Fraunces } from "next/font/google"
import { notFound } from "next/navigation"

import { createClient } from "@/lib/supabase/server"
import { isOwner } from "@/lib/permissions"
import { AtmosphereBackdrop } from "@/components/atmosphere-backdrop"
import { Badge } from "@/components/ui/badge"

const fraunces = Fraunces({ subsets: ["latin"], variable: "--font-fraunces", display: "swap" })
const dmSerif = DM_Serif_Display({ subsets: ["latin"], weight: "400", variable: "--font-dmserif", display: "swap" })

export const metadata = { title: "Full preview · Christopher Edwards Financial Associates" }

const DISPLAY: CSSProperties = { fontFamily: "var(--font-fraunces)" }
const SERIF: CSSProperties = { fontFamily: "var(--font-dmserif)" }

// Ultraviolet Terminal — the chosen scheme, applied to this whole preview via
// scoped token overrides (dark + the light-mode counterparts for the light card).
const ULTRA: CSSProperties = {
  "--background": "#0B0A12", "--card": "#15131F", "--popover": "#1C1930",
  "--foreground": "#ECE9F5", "--muted-foreground": "#9A93B5", "--border": "rgba(255,255,255,0.09)",
  "--secondary": "#1C1930", "--highlight": "#A78BFA", "--highlight-foreground": "#0B0A12",
  "--gain": "#34D399", "--loss": "#FB7185", "--warning": "#FBBF24", "--ring": "#A78BFA",
} as CSSProperties

const NAV = ["Today", "Focus list", "Emerging", "Commodities", "Scan history"]

type Pick = { t: string; co: string; grade: "A" | "B" | "C"; price: string; chg: number; thesis: string }
const PICKS: Pick[] = [
  { t: "NVDA", co: "NVIDIA", grade: "A", price: "1,182.50", chg: 2.41, thesis: "Datacenter demand still outrunning supply; pricing power intact into the next cycle." },
  { t: "MSFT", co: "Microsoft", grade: "A", price: "498.13", chg: 0.62, thesis: "Copilot attach accelerating; Azure reaccelerating off a higher base." },
  { t: "XOM", co: "Exxon Mobil", grade: "B", price: "121.04", chg: -1.18, thesis: "Buybacks underpin the floor; refining margins normalizing off 2025 highs." },
  { t: "CVS", co: "CVS Health", grade: "C", price: "58.27", chg: -3.42, thesis: "Reimbursement pressure persists; turnaround unproven, on the watch tier." },
]

function Grade({ g }: { g: "A" | "B" | "C" }) {
  return <Badge variant={g === "A" ? "gradeA" : g === "B" ? "gradeB" : "gradeC"}>{g}</Badge>
}

function Chg({ v }: { v: number }) {
  return (
    <span className={`text-data text-[14px] ${v >= 0 ? "text-gain" : "text-loss"}`}>
      {v >= 0 ? "▲ +" : "▼ "}{Math.abs(v).toFixed(2)}%
    </span>
  )
}

export default async function FullPreview() {
  // Internal design reference / mock content -- NOT part of the public product.
  // Gate it to the owner so it isn't reachable by the public at launch.
  const supabase = await createClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!isOwner(user)) notFound()

  return (
    <div className={`${fraunces.variable} ${dmSerif.variable} relative min-h-screen`} style={ULTRA}>
      <AtmosphereBackdrop src="/floor/wallstreet.webp" variant="atmosphere" position="center 52%" />

      {/* ── faux top bar ── */}
      <header className="sticky top-0 z-30 border-b border-border bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-[1180px] items-center justify-between gap-6 px-6">
          <div className="flex items-center gap-2">
            <span className="inline-block size-2 rounded-[2px] bg-highlight" />
            <span className="text-[15px] font-semibold tracking-tight text-foreground">Christopher Edwards Financial Associates</span>
          </div>
          <nav className="hidden items-center gap-1 md:flex">
            {NAV.map((n, i) => (
              <span key={n} className={`rounded-md px-3 py-1.5 text-[13.5px] ${i === 0 ? "text-foreground" : "text-muted-foreground"}`}>{n}</span>
            ))}
          </nav>
          <span className="text-data text-[12px] text-muted-foreground">NYC 09:31 ET · delayed</span>
        </div>
      </header>

      <div className="mx-auto max-w-[1180px] space-y-24 px-6 py-16 md:px-10">
        <p className="text-eyebrow -mb-16">Full preview · Ultraviolet Terminal · not yet live</p>

        {/* ── LANDING HERO ── */}
        <section className="space-y-6 pt-10">
          <p className="text-eyebrow text-highlight/90">Institutional research, for advisors</p>
          <h1 className="max-w-[15ch] text-[56px] leading-[1.02] tracking-[-0.025em] text-foreground md:text-[78px]" style={DISPLAY}>
            Three thousand stocks. Fifteen convictions.
          </h1>
          <p className="max-w-[560px] text-[20px] leading-relaxed text-muted-foreground" style={SERIF}>
            The full liquid US market, scanned three times every trading day.
            Every claim traced to its source, every number you can trust.
          </p>
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <button className="rounded-lg bg-highlight px-5 py-2.5 text-[14px] font-semibold text-[var(--highlight-foreground)] transition-premium hover:opacity-90">
              Request access
            </button>
            <button className="rounded-lg border border-border px-5 py-2.5 text-[14px] font-medium text-foreground transition-premium hover:bg-secondary">
              See a sample dossier
            </button>
          </div>
        </section>

        {/* ── DASHBOARD: TODAY ── */}
        <section className="space-y-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="text-eyebrow">Dashboard · Today</p>
              <h2 className="text-[34px] tracking-[-0.02em] text-foreground" style={DISPLAY}>The week the dollar turned</h2>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="accent">● Regime · Risk-on</Badge>
              <span className="text-data text-[12px] text-muted-foreground">scanned 3,304 → 35 → 15</span>
            </div>
          </div>

          {/* focus cards (glass) */}
          <div className="grid gap-5 md:grid-cols-2">
            {PICKS.slice(0, 2).map((p) => (
              <div key={p.t} className="glass-card rounded-xl p-5">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2.5">
                    <span className="text-data text-[20px] font-semibold text-foreground">{p.t}</span>
                    <Grade g={p.grade} />
                  </div>
                  <div className="text-right">
                    <p className="text-data text-[15px] text-foreground">{p.price}</p>
                    <Chg v={p.chg} />
                  </div>
                </div>
                <p className="mt-3 text-[15px] leading-relaxed text-muted-foreground" style={SERIF}>{p.thesis}</p>
              </div>
            ))}
          </div>

          {/* focus list table on a solid card */}
          <div className="overflow-hidden rounded-xl border border-border bg-card">
            <div className="border-b border-border px-5 py-3">
              <p className="text-eyebrow">Focus list · 18 names</p>
            </div>
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border">
                  {["Ticker", "Company", "Price", "Day %", "Grade"].map((h) => (
                    <th key={h} className="text-eyebrow px-5 py-2.5 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {PICKS.map((p) => (
                  <tr key={p.t} className="border-b border-border/50 last:border-0">
                    <td className="text-data px-5 py-3 text-[15px] font-semibold text-foreground">{p.t}</td>
                    <td className="px-5 py-3 text-[14px] text-muted-foreground" style={SERIF}>{p.co}</td>
                    <td className="text-data px-5 py-3 text-[15px] text-foreground">{p.price}</td>
                    <td className="px-5 py-3"><Chg v={p.chg} /></td>
                    <td className="px-5 py-3"><Grade g={p.grade} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* ── COMMODITIES (dual-horizon) ── */}
        <section className="space-y-6">
          <p className="text-eyebrow">Dashboard · Commodities</p>
          <div className="grid gap-5 lg:grid-cols-2">
            <div className="glass-card rounded-xl p-6">
              <div className="flex items-center justify-between">
                <h3 className="text-[22px] text-foreground" style={DISPLAY}>Crude Oil · WTI</h3>
                <span className="text-data text-[18px] text-foreground">85.42</span>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Badge variant="warning">Tightening</Badge>
                <Badge variant="gradeC">Backwardation</Badge>
                <Badge variant="accent">Specs net long</Badge>
              </div>
              <p className="mt-4 text-[15px] leading-relaxed text-muted-foreground" style={SERIF}>
                Tactical: inventories below the 5-yr average and a backwardated
                curve point to near-term tightness.
              </p>
            </div>
            <div className="glass-card rounded-xl p-6">
              <div className="flex items-center justify-between">
                <h3 className="text-[22px] text-foreground" style={DISPLAY}>Gold</h3>
                <span className="text-data text-[18px] text-foreground">4,216.30</span>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Badge variant="neutral">S/D unverified</Badge>
                <Badge variant="gradeC">Contango</Badge>
                <Badge variant="destructive">Real-rate headwind</Badge>
              </div>
              <p className="mt-4 text-[15px] leading-relaxed text-muted-foreground" style={SERIF}>
                Structural: rising real yields and a firmer dollar are a headwind;
                supply/demand is gated, so this read leans on macro.
              </p>
            </div>
          </div>
        </section>

        <footer className="border-t border-border pt-8">
          <p className="text-caption max-w-[680px]" style={SERIF}>
            This is a faithful mock of the live pages in the new identity:
            Ultraviolet Terminal · Fraunces titles · DM Serif body · Geist Mono
            data · glass cards · the Wall St signpost dimmed behind it. Approve and
            I apply it to the real landing, auth, and dashboard, then run the
            AA-contrast + mobile-375 pass. The light/auth pages get the matching
            light-mode counterpart of this palette.
          </p>
        </footer>
      </div>
    </div>
  )
}
