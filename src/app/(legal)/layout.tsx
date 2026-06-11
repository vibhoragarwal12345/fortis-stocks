import Link from "next/link"

import { AuroraField } from "@/components/ui/aurora-field"
import { CursorGlow } from "@/components/ui/cursor-glow"
import { SiteFooter } from "@/components/site-footer"

// Shared chrome for the public legal pages (/privacy, /terms, /disclaimer):
// a minimal wordmark that returns home, a readable measure for long-form
// copy, and the site footer with the cross-links between them.

export default function LegalLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <div className="relative flex min-h-screen flex-col">
      <CursorGlow />
      <div aria-hidden className="pointer-events-none fixed inset-0 -z-10">
        <AuroraField />
      </div>

      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-[760px] items-center px-6">
          <Link
            href="/"
            className="flex items-center gap-2.5 transition-premium hover:opacity-80"
          >
            <span aria-hidden className="size-2 rounded-[2px] bg-highlight" />
            <span className="text-[13px] font-semibold uppercase tracking-[0.22em]">
              Fortis
            </span>
          </Link>
        </div>
      </header>

      <main className="flex-1">
        <article className="mx-auto max-w-[760px] px-6 py-16 md:py-20">
          {children}
        </article>
      </main>

      <SiteFooter />
    </div>
  )
}
