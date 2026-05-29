import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

// Premium-tier Button.
//
// Variants are restrained on purpose:
//   default      Solid near-black (light) / near-white (dark). The hero CTA.
//   outline      Bordered, transparent background. The neutral action.
//   ghost        No chrome at all; reveals a subtle bg on hover.
//   link         Text-only with hover-underline. For inline calls-to-action.
//   secondary    Soft surface (#F5F5F7). Use for quiet secondary actions.
//   destructive  Reserved for "delete X" -- quiet, never red-on-red.
//
// Motion: --duration-fast (200ms) on --ease-out-quart (Apple's curve).
// Active state nudges the button down 1px instead of using harsh box-shadow.

const buttonVariants = cva(
  "group/button inline-flex shrink-0 items-center justify-center gap-2 " +
    "rounded-md font-medium tracking-tight whitespace-nowrap " +
    "transition-premium select-none outline-none " +
    "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 " +
    "focus-visible:ring-offset-background " +
    "active:translate-y-px " +
    "disabled:pointer-events-none disabled:opacity-40 " +
    "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/85",
        outline:
          "border border-border bg-background text-foreground hover:bg-secondary",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        ghost:
          "text-foreground hover:bg-secondary",
        destructive:
          "border border-destructive/30 bg-background text-destructive hover:bg-destructive/5",
        link:
          "h-auto !p-0 text-foreground underline-offset-4 hover:underline active:translate-y-0",
      },
      size: {
        default: "h-9 px-4 text-[14px]",
        sm:      "h-8 px-3 text-[13px]",
        xs:      "h-7 px-2.5 text-[12px]",
        lg:      "h-11 px-6 text-[15px]",
        xl:      "h-12 px-7 text-[16px]",
        icon:    "size-9",
        "icon-sm": "size-8",
        "icon-xs": "size-7",
        "icon-lg": "size-11",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
)

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
