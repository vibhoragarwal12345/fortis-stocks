import * as React from "react"

import { cn } from "@/lib/utils"

// Aurora light field — the signature backdrop for dark hero surfaces.
//
// Three blurred light blobs (cyan / indigo / sea-green) drift on slow,
// offset ease curves over a hairline blueprint grid, under a film-grain
// pass that keeps the gradients from reading as flat digital banding.
//
// Engineering notes:
//   - Pure CSS transforms — compositor-only, no canvas, no rAF, no JS.
//   - Drift freezes on <=768px screens (style holds, motion stops).
//   - prefers-reduced-motion collapses it via the global clamp.
//   - Always render inside `position: relative; overflow: hidden`.
//
//   <div className="relative overflow-hidden">
//     <AuroraField />
//     <Section className="relative">…</Section>
//   </div>

export function AuroraField({
  className,
  grid = true,
}: {
  className?: string
  /** Render the faint blueprint grid layer. Default true. */
  grid?: boolean
}) {
  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none absolute inset-0 overflow-hidden",
        className,
      )}
    >
      <div className="aurora-blob aurora-blob-1" />
      <div className="aurora-blob aurora-blob-2" />
      <div className="aurora-blob aurora-blob-3" />
      {grid ? <div className="aurora-grid" /> : null}
      <div className="aurora-grain" />
    </div>
  )
}
