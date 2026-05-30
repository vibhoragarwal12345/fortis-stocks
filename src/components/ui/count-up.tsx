"use client"

import * as React from "react"

// rAF-based count-up. Animates from 0 to `value` once when the element
// first scrolls into view. Respects prefers-reduced-motion (renders the
// final value immediately, no animation).
//
//   <CountUp value={47} format={(n) => `${n.toFixed(0)}%`} />

type FormatFn = (n: number) => string

type CountUpProps = {
  value: number
  duration?: number          // ms, default 700
  format?: FormatFn          // default: floor + locale-string
  className?: string
  startOnView?: boolean      // default true; if false, fires on mount
}

const EASE = (t: number) => 1 - Math.pow(1 - t, 3) // ease-out cubic

const DEFAULT_FORMAT: FormatFn = (n) =>
  Math.floor(n).toLocaleString(undefined, { maximumFractionDigits: 0 })

export function CountUp({
  value,
  duration = 700,
  format = DEFAULT_FORMAT,
  className,
  startOnView = true,
}: CountUpProps) {
  const ref = React.useRef<HTMLSpanElement>(null)
  const [display, setDisplay] = React.useState<string>(() => format(0))
  const [started, setStarted] = React.useState(false)
  const [reduced, setReduced] = React.useState(false)

  // Reduced-motion: skip the animation, jump straight to the target.
  React.useEffect(() => {
    if (typeof window === "undefined") return
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)")
    setReduced(mq.matches)
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener?.("change", onChange)
    return () => mq.removeEventListener?.("change", onChange)
  }, [])

  // Trigger: either on viewport entry or on mount.
  React.useEffect(() => {
    if (started) return
    if (reduced) {
      setStarted(true)
      setDisplay(format(value))
      return
    }
    if (!startOnView) {
      setStarted(true)
      return
    }
    const node = ref.current
    if (!node || typeof IntersectionObserver === "undefined") {
      setStarted(true)
      return
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
  }, [reduced, startOnView, started, value, format])

  // The rAF loop. Runs once `started` flips true.
  React.useEffect(() => {
    if (!started || reduced) return
    let raf = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - t0) / duration)
      const v = value * EASE(t)
      setDisplay(format(v))
      if (t < 1) raf = requestAnimationFrame(tick)
      else setDisplay(format(value))
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [started, reduced, value, duration, format])

  return (
    <span ref={ref} className={className}>
      {display}
    </span>
  )
}
