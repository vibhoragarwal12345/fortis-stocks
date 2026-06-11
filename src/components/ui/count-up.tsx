"use client"

import * as React from "react"

import { useMediaQuery } from "@/hooks/use-media-query"

// rAF-based count-up. Animates from 0 to `value` once when the element
// first scrolls into view. Respects prefers-reduced-motion (renders the
// final value immediately, no animation).
//
// Formatting is declarative (serializable props, not a function) so server
// components can render <CountUp> directly:
//
//   <CountUp value={3312} />                          → 3,312
//   <CountUp value={47} suffix="%" />                 → 47%
//   <CountUp value={1.86} signed decimals={2} suffix="%" />  → +1.86%

type CountUpProps = {
  value: number
  duration?: number          // ms, default 700
  decimals?: number          // fixed decimal places; 0 = floor + thousands grouping
  signed?: boolean           // prefix "+" on non-negative values
  prefix?: string
  suffix?: string
  className?: string
  startOnView?: boolean      // default true; if false, fires on mount
}

const EASE = (t: number) => 1 - Math.pow(1 - t, 3) // ease-out cubic

export function CountUp({
  value,
  duration = 700,
  decimals = 0,
  signed = false,
  prefix = "",
  suffix = "",
  className,
  startOnView = true,
}: CountUpProps) {
  const format = React.useCallback(
    (n: number) => {
      const body =
        decimals > 0
          ? n.toFixed(decimals)
          : Math.floor(n).toLocaleString(undefined, {
              maximumFractionDigits: 0,
            })
      const sign = signed && n >= 0 ? "+" : ""
      return `${prefix}${sign}${body}${suffix}`
    },
    [decimals, signed, prefix, suffix],
  )

  const ref = React.useRef<HTMLSpanElement>(null)
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)")
  // startOnView=false starts the animation on mount.
  const [started, setStarted] = React.useState(!startOnView)
  const [progress, setProgress] = React.useState(0) // 0..1, eased externally

  // Viewport trigger. All setState happens inside observer/timeout
  // callbacks, never the effect body.
  React.useEffect(() => {
    if (started || reduced) return
    const node = ref.current
    if (!node || typeof IntersectionObserver === "undefined") {
      const t = setTimeout(() => setStarted(true), 0)
      return () => clearTimeout(t)
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setStarted(true)
            io.disconnect()
          }
        }
      },
      { threshold: 0.2 },
    )
    io.observe(node)
    return () => io.disconnect()
  }, [reduced, started])

  // The rAF loop. Runs once `started` flips true.
  React.useEffect(() => {
    if (!started || reduced) return
    let raf = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - t0) / duration)
      setProgress(t)
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [started, reduced, duration])

  // Reduced motion (or a finished run) renders the exact final value.
  const display =
    reduced || progress >= 1
      ? format(value)
      : format(value * EASE(started ? progress : 0))

  return (
    <span ref={ref} className={className}>
      {display}
    </span>
  )
}
