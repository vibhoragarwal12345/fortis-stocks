"use client"

// Top-level boundary: catches errors thrown in the ROOT layout itself, which
// the per-segment app/error.tsx cannot reach. global-error replaces the whole
// document (no root layout, so globals.css may not apply) — hence inline
// styles. Rare path; keep it dependency-free and self-contained.

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#0b0a12",
          color: "#e7e5ee",
          fontFamily:
            "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
          padding: "1.5rem",
        }}
      >
        <div style={{ maxWidth: 420, textAlign: "center" }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, margin: "0 0 8px" }}>
            Something went wrong
          </h1>
          <p style={{ fontSize: 14, color: "#a9a6b8", lineHeight: 1.6, margin: 0 }}>
            A top-level error occurred. Try again, and if it persists the rest
            of the product is unaffected.
          </p>
          {error.digest && (
            <p style={{ fontSize: 11, color: "#6f6c80", marginTop: 12 }}>
              ref {error.digest}
            </p>
          )}
          <button
            type="button"
            onClick={() => reset()}
            style={{
              marginTop: 24,
              padding: "8px 16px",
              fontSize: 14,
              fontWeight: 500,
              color: "#0b0a12",
              background: "#a78bfa",
              border: "none",
              borderRadius: 8,
              cursor: "pointer",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  )
}
