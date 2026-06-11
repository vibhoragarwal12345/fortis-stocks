import * as React from "react"

import { cn } from "@/lib/utils"

// Apple-style headline reveal: each word rises out of its own overflow
// mask on the system curve, staggered left to right. Pure CSS animation —
// server-renderable, zero hydration cost, instant under reduced motion
// (the global clamp snaps every word to its final position).
//
// Screen readers get the full sentence via aria-label; the animated
// fragments are hidden from the accessibility tree.
//
//   <WordReveal as="h1" className="text-display" text="Signal, not noise." />
//   <WordReveal text="Slower, later" delay={200} step={90} />

type WordRevealProps = {
  text: string
  as?: "h1" | "h2" | "h3" | "p" | "span"
  className?: string
  /** ms before the first word starts. Default 0. */
  delay?: number
  /** ms between words. Default 70. */
  step?: number
}

export function WordReveal({
  text,
  as: Tag = "h1",
  className,
  delay = 0,
  step = 70,
}: WordRevealProps) {
  const words = text.split(" ")
  return (
    <Tag className={cn(className)} aria-label={text}>
      {words.map((word, i) => (
        <span key={`${word}-${i}`} className="word-reveal-mask" aria-hidden>
          <span
            className="word-reveal"
            style={{ animationDelay: `${delay + i * step}ms` }}
          >
            {word}
            {i < words.length - 1 ? " " : ""}
          </span>
        </span>
      ))}
    </Tag>
  )
}
