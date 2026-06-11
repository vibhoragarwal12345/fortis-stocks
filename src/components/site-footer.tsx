import Link from "next/link"

// Site-wide footer. Rendered at the bottom of every surface (marketing,
// auth, dashboard, admin, legal) so the legal pages are always one click
// away. Server component — no interactivity, no client bundle.

const LEGAL_LINKS = [
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
  { href: "/disclaimer", label: "Disclaimer" },
] as const

export function SiteFooter({
  brandName = "Fortis Stock Intelligence",
}: {
  brandName?: string
}) {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto flex max-w-[1280px] flex-col gap-3 px-6 py-6 md:flex-row md:items-center md:justify-between md:px-10">
        <p className="text-caption">
          &copy; {new Date().getFullYear()} {brandName} · For licensed advisor
          review — not investment advice.
        </p>
        <nav
          aria-label="Legal"
          className="flex flex-wrap items-center gap-x-5 gap-y-1 text-caption"
        >
          {LEGAL_LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="underline-offset-4 hover:text-foreground hover:underline"
            >
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
    </footer>
  )
}
