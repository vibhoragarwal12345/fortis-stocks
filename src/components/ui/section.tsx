import * as React from "react"

import { cn } from "@/lib/utils"

// Apple-tier section wrapper.
//
// Consistent vertical rhythm (96-128px desktop, 56-72px mobile) and a hard
// 1120px content max-width. Use this around every top-level page section
// instead of hand-rolling px/py utilities so spacing stays consistent across
// the product.
//
//   <Section>                       // default 96px vertical
//   <Section spacing="hero">        // 128px vertical, for the first section
//   <Section spacing="compact">     // 56px, for dense pages
//   <Section width="narrow">        // 720px content (long-form prose)
//   <Section width="wide">          // 1280px (data tables)

const spacingClasses = {
  hero:    "py-20 md:py-32",
  default: "py-16 md:py-24",
  compact: "py-10 md:py-14",
  none:    "py-0",
} as const

const widthClasses = {
  narrow:  "max-w-[720px]",
  default: "max-w-[1120px]",
  wide:    "max-w-[1280px]",
  full:    "max-w-none",
} as const

type SectionProps = React.ComponentProps<"section"> & {
  spacing?: keyof typeof spacingClasses
  width?:   keyof typeof widthClasses
  /**
   * If true, the section's background uses the elevated card color rather
   * than the page background -- useful for alternating bands.
   */
  surface?: boolean
}

function Section({
  className,
  spacing = "default",
  width = "default",
  surface = false,
  children,
  ...props
}: SectionProps) {
  return (
    <section
      data-slot="section"
      className={cn(
        "w-full",
        surface && "bg-card",
        spacingClasses[spacing],
        className,
      )}
      {...props}
    >
      <div className={cn("mx-auto px-6 md:px-10", widthClasses[width])}>
        {children}
      </div>
    </section>
  )
}

export { Section }
