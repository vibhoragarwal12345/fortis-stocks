"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

// Cursor-tracked spotlight card.
//
// As the pointer moves, an interior light pool and a lit 1px border
// segment follow it — the surface feels reactive without ever changing
// layout. The visuals live in globals.css (.glow-card::before/::after);
// this component only streams pointer coordinates into CSS vars.
//
// Touch devices and reduced-motion get a plain card (effect layers are
// display:none under `hover: none`; opacity transitions are clamped).
//
//   <GlowCard className="rounded-xl border border-border bg-card p-8">
//     …
//   </GlowCard>

export function GlowCard({
  className,
  children,
  ...props
}: React.ComponentProps<"div">) {
  const ref = React.useRef<HTMLDivElement>(null)

  const onPointerMove = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const node = ref.current
      if (!node || e.pointerType !== "mouse") return
      const rect = node.getBoundingClientRect()
      node.style.setProperty("--glow-x", `${e.clientX - rect.left}px`)
      node.style.setProperty("--glow-y", `${e.clientY - rect.top}px`)
    },
    [],
  )

  return (
    <div
      ref={ref}
      onPointerMove={onPointerMove}
      className={cn("glow-card", className)}
      {...props}
    >
      {children}
    </div>
  )
}
