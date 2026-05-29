"use client"

export default function AdminError({
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
          Admin page error
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          A query against the platform tables failed.
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
