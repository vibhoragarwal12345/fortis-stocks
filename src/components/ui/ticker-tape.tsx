import * as React from "react"
import Link from "next/link"

import { cn } from "@/lib/utils"

// Seamless market tape. The item strip renders twice and translates -50%
// on a linear loop, so the seam never shows. Edge fade via mask-image,
// pauses on hover, sits still under reduced motion. Pure CSS — server-
// renderable. Items with an href become links (tape pauses on hover, so
// the moving targets are clickable).
//
//   <TickerTape items={[{ ticker: "NVDA", price: "1,184.20", change: 2.41 }]} />

export type TapeItem = {
  ticker: string
  price: string
  /** Signed percent, e.g. 2.41 or -0.62. */
  change: number
  /** Optional click-through (e.g. /dashboard/research/NVDA). */
  href?: string
}

export function TickerTape({
  items,
  className,
}: {
  items: TapeItem[]
  className?: string
}) {
  // Two copies make the loop seamless; the duplicate is decorative.
  const strips: { copy: TapeItem[]; hidden: boolean }[] = [
    { copy: items, hidden: false },
    { copy: items, hidden: true },
  ]
  return (
    <div
      className={cn(
        "ticker-tape-mask border-y border-border bg-card/40",
        className,
      )}
    >
      <div className="ticker-tape">
        {strips.map((strip, s) => (
          <div
            key={s}
            aria-hidden={strip.hidden}
            className="flex shrink-0 items-center"
          >
            {strip.copy.map((item) => {
              const body = (
                <>
                  <span className="font-medium text-foreground">
                    {item.ticker}
                  </span>
                  <span className="text-muted-foreground">{item.price}</span>
                  <span
                    className={item.change >= 0 ? "text-gain" : "text-loss"}
                  >
                    {item.change >= 0 ? "▲" : "▼"}{" "}
                    {Math.abs(item.change).toFixed(2)}%
                  </span>
                </>
              )
              const itemCls =
                "text-data inline-flex items-center gap-2.5 px-7 py-3 text-[13px]"
              return item.href && !strip.hidden ? (
                <Link
                  key={`${s}-${item.ticker}`}
                  href={item.href}
                  className={cn(
                    itemCls,
                    "transition-premium hover:bg-secondary/50",
                  )}
                >
                  {body}
                </Link>
              ) : (
                <span key={`${s}-${item.ticker}`} className={itemCls}>
                  {body}
                </span>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}
