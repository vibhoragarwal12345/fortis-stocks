"use client"

// Global error boundary. Triggered when any unhandled error escapes a route
// segment. Without this, Next.js renders its default branded "Application
// error" page. With it, the user gets a graceful card and a Try-again button
// while we keep the rest of the shell intact.

import Link from "next/link"

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md rounded-lg border bg-card p-8 text-center shadow-sm">
        <h1 className="text-xl font-semibold tracking-tight">Something went wrong</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          A page-level error escaped without a more specific handler. The
          rest of the product is fine. Try again or head back to the
          dashboard.
        </p>
        {error.digest && (
          <p className="mt-3 font-mono text-[10px] text-muted-foreground">
            ref {error.digest}
          </p>
        )}
        <div className="mt-6 flex justify-center gap-3">
          <button
            type="button"
            onClick={() => reset()}
            className="rounded-md border bg-foreground px-4 py-2 text-sm font-medium text-background hover:bg-foreground/90"
          >
            Try again
          </button>
          <Link
            href="/dashboard"
            className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-muted"
          >
            Dashboard
          </Link>
        </div>
      </div>
    </div>
  )
}
