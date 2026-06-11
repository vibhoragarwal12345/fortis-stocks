import * as React from "react"

import { cn } from "@/lib/utils"

// Loading placeholder — a slow shimmer sweep, never a spinner. The
// .skeleton-shimmer keyframes live in globals.css and are clamped by
// prefers-reduced-motion (degrades to a static tinted block).
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("skeleton-shimmer rounded-md", className)}
      {...props}
    />
  )
}

export { Skeleton }
