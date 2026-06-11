import Link from "next/link"

import { SiteFooter } from "@/components/site-footer"

// Shared chrome for the public legal pages (/privacy, /terms, /disclaimer):
// a minimal wordmark that returns home, a readable measure for long-form
// copy, and the site footer with the cross-links between them.

export default function LegalLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-[760px] items-center px-6">
          <Link
            href="/"
            className="flex items-center gap-2 transition-premium hover:opacity-80"
          >
            <span
              aria-hidden
              className="inline-block h-2.5 w-2.5 rounded-sm bg-foreground"
            />
            <span className="text-[14px] font-semibold tracking-tight text-foreground">
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
