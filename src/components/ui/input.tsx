import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"

import { cn } from "@/lib/utils"

// Premium-tier input.
//
//   Height                 44px (h-11) -- generous touch target, matches the
//                          default Button size for clean visual alignment.
//   Border                 1px var(--border). Becomes near-black (--ring)
//                          on focus, with a soft 2px ring-offset for breath.
//   Background             transparent on light, transparent on dark --
//                          inherits from the surrounding card.
//   Type                   15px, weight 400. tracking-tight pairs with the
//                          form labels which sit at small/13px.
//   Transition             200ms on the design-system curve.

function Input({
  className,
  type,
  ...props
}: React.ComponentProps<"input">) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(
        "h-11 w-full min-w-0 rounded-md border border-border bg-transparent " +
          "px-3.5 text-[15px] tracking-tight text-foreground " +
          "transition-premium outline-none " +
          "placeholder:text-muted-foreground/70 " +
          "hover:border-foreground/30 " +
          "focus:border-ring focus:ring-2 focus:ring-ring/20 focus:ring-offset-0 " +
          "focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20 " +
          "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 " +
          "aria-invalid:border-destructive aria-invalid:ring-2 aria-invalid:ring-destructive/20 " +
          "file:inline-flex file:h-9 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground",
        className,
      )}
      {...props}
    />
  )
}

export { Input }
