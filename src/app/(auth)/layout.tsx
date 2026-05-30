import Link from "next/link"

import { DottedSurface } from "@/components/ui/dotted-surface"

// Auth layout. Centered, minimal, generous whitespace. The dotted surface
// sits behind everything as the signature visual; a soft radial vignette
// behind the form keeps the inputs perfectly readable.

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="relative flex min-h-screen flex-col items-center overflow-hidden bg-background px-6 py-16 md:py-24">
      <DottedSurface className="absolute inset-0 -z-10" />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 [background:radial-gradient(ellipse_at_center,var(--background)_0%,transparent_70%)]"
      />

      <Link
        href="/"
        className="relative flex items-center gap-2 transition-premium hover:opacity-80"
      >
        <span
          aria-hidden
          className="inline-block h-2.5 w-2.5 rounded-sm bg-foreground"
        />
        <span className="text-caption uppercase tracking-[0.18em] text-foreground">
          Fortis
        </span>
      </Link>

      <div className="relative mt-14 w-full max-w-[400px] md:mt-20">
        {children}
      </div>

      <p className="relative mt-auto pt-16 text-caption">
        For licensed advisor review — not investment advice.
      </p>
    </div>
  )
}
