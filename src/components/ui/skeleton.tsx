import * as React from "react"

import { cn } from "@/lib/utils"

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn(
        "animate-pulse rounded-md bg-secondary/70 [animation-duration:1800ms]",
        className,
      )}
      {...props}
    />
  )
}

export { Skeleton }
