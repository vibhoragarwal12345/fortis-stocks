"use client"

import * as React from "react"
import { usePathname } from "next/navigation"

import { cn } from "@/lib/utils"

// Cross-route fade. Wrap a layout's {children} in this; whenever the
// pathname changes, the key flips and React remounts the subtree which
// re-fires the CSS-defined fade-in animation.
//
// Cheap, deterministic, respects prefers-reduced-motion via the
// @media block in globals.css that clamps every transition to 0.01ms.

export function PageTransition({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  const pathname = usePathname()
  return (
    <div
      key={pathname}
      className={cn(
        "animate-[pageFade_300ms_cubic-bezier(0.16,1,0.3,1)_both]",
        className,
      )}
    >
      {children}
    </div>
  )
}
