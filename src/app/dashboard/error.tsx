"use client"

// Scoped error boundary for /dashboard/*. Keeps the dashboard chrome
// (header, footer) intact while showing a graceful card for the failed
// page segment. Without this a single failed Supabase query blanks the
// whole layout.

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <div className="rounded-lg border bg-card p-8 text-center shadow-sm">
        <h2 className="text-lg font-semibold tracking-tight">
          This page failed to load
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          A data query came back with an error. Try refreshing. The rest of
          your dashboard is still working.
        </p>
        {error.digest && (
          <p className="mt-3 font-mono text-[10px] text-muted-foreground">
            ref {error.digest}
          </p>
        )}
        <button
          type="button"
          onClick={() => reset()}
          className="mt-6 rounded-md border bg-foreground px-4 py-2 text-sm font-medium text-background hover:bg-foreground/90"
        >
          Try again
        </button>
      </div>
    </div>
  )
}
