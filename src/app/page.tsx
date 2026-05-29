import Link from "next/link"

// Intentionally minimal gateway. NOT a marketing/pricing page.
// While the product is in invite-only pilot we don't want a public funnel.
export default function Home() {
  return (
    <main
      className="flex min-h-screen flex-col items-center justify-center px-4"
      style={{ backgroundColor: "#0F1F1A", color: "#F4EFE3" }}
    >
      <div className="flex flex-col items-center gap-8 text-center">
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-3 w-3 rounded-sm"
            style={{ backgroundColor: "#0F6E56" }}
          />
          <span className="text-sm font-semibold tracking-[0.18em] uppercase">
            Fortis
          </span>
        </div>
        <h1 className="text-4xl sm:text-5xl font-semibold tracking-tight">
          Fortis Stock Intelligence
        </h1>
        <p className="max-w-md text-base sm:text-lg" style={{ color: "#C8C2B0" }}>
          Institutional research for wealth advisors.
        </p>
        <Link
          href="/login"
          className="inline-flex items-center justify-center rounded-md px-6 py-2.5 text-sm font-medium transition-colors"
          style={{ backgroundColor: "#0F6E56", color: "#F4EFE3" }}
        >
          Sign in
        </Link>
        <p className="text-xs" style={{ color: "#7A8A82" }}>
          Access is currently invite-only.
        </p>
      </div>
    </main>
  )
}
