import Link from "next/link"

// Auth layout. Centered, minimal, generous whitespace. The wordmark sits
// above the form as a quiet brand anchor, and a thin footer line below
// keeps the surface intentional rather than empty.

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="relative flex min-h-screen flex-col items-center bg-background px-6 py-16 md:py-24">
      <Link
        href="/"
        className="flex items-center gap-2 transition-premium hover:opacity-80"
      >
        <span
          aria-hidden
          className="inline-block h-2.5 w-2.5 rounded-sm bg-foreground"
        />
        <span className="text-caption uppercase tracking-[0.18em] text-foreground">
          Fortis
        </span>
      </Link>

      <div className="mt-14 w-full max-w-[400px] md:mt-20">{children}</div>

      <p className="mt-auto pt-16 text-caption">
        For licensed advisor review — not investment advice.
      </p>
    </div>
  )
}
