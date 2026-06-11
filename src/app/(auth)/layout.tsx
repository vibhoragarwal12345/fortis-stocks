import Link from "next/link"

import { AuroraField } from "@/components/ui/aurora-field"
import { CursorGlow } from "@/components/ui/cursor-glow"
import { SiteFooter } from "@/components/site-footer"

// Auth layout. The same aurora identity as the gateway, with the form on
// a glass card so the light field stays visible without ever competing
// with the inputs. Wordmark matches the landing top bar exactly. The
// SiteFooter keeps the legal pages one click away from sign-in.

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="relative flex min-h-svh flex-col items-center overflow-hidden bg-background px-6 py-10 md:py-14">
      <CursorGlow />
      <AuroraField />

      <Link
        href="/"
        className="relative flex items-center gap-2.5 transition-premium hover:opacity-80"
      >
        <span aria-hidden className="size-2 rounded-[2px] bg-highlight" />
        <span className="text-[13px] font-semibold uppercase tracking-[0.22em]">
          Fortis
        </span>
      </Link>

      <div className="relative mt-12 w-full max-w-[420px] md:mt-16">
        <div className="rounded-2xl border border-border bg-card/75 p-8 shadow-[var(--shadow-lg)] backdrop-blur-xl md:p-10">
          {children}
        </div>
      </div>

      <div className="relative mt-auto w-full pt-12">
        <SiteFooter />
      </div>
    </div>
  )
}
