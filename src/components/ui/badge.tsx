import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

// Badges are pills built entirely from system tokens so they render
// correctly in dark mode and light mode.
//
// Semantic rules:
//   accent / gradeA   the one cyan accent — signal, top conviction
//   success / gain    P&L green — returns, positive deltas
//   destructive/loss  P&L red — drawdowns, negative deltas
//   warning / gradeB  amber — caution tier
//   neutral / gradeC  muted — informational, lowest tier

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 " +
    "text-[11px] font-semibold uppercase tracking-[0.08em] " +
    "transition-premium whitespace-nowrap",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border bg-transparent text-foreground",
        accent: "border-highlight/25 bg-highlight/10 text-highlight",
        success: "border-gain/25 bg-gain/10 text-gain",
        warning: "border-warning/25 bg-warning/10 text-warning",
        destructive: "border-loss/25 bg-loss/10 text-loss",
        neutral: "border-border bg-secondary/60 text-muted-foreground",
        info: "border-highlight/25 bg-highlight/10 text-highlight",
        gradeA: "border-highlight/30 bg-highlight/12 text-highlight",
        gradeB: "border-warning/25 bg-warning/10 text-warning",
        gradeC: "border-border bg-secondary/60 text-muted-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  }
)

function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return (
    <span
      data-slot="badge"
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
