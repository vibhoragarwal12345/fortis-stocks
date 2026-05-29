"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

// Scroll reveal primitive.
//
// Fades opacity 0 -> 1 and translates 12px -> 0 the FIRST time the element
// enters the viewport. Never replays on subsequent scrolls.
//
// All motion is gated on prefers-reduced-motion -- if the user has it
// enabled, the element renders fully visible immediately with no animation.
//
//   <Reveal>          // default: in immediately as it crosses 15% of viewport
//   <Reveal delay={80}>   // stagger groups by passing an incrementing delay
//   <Reveal as="li">  // render any element

type RevealProps = React.ComponentPropsWithoutRef<"div"> & {
  /** Stagger amount in ms. Apply incrementing values to siblings. */
  delay?: number
  /** % of viewport visibility before triggering. Default 0.15. */
  threshold?: number
  /** Tag to render. Default "div". */
  as?: "div" | "section" | "article" | "li" | "ul" | "header" | "main" | "p"
}

export function Reveal({
  delay = 0,
  threshold = 0.15,
  as = "div",
  className,
  style,
  children,
  ...rest
}: RevealProps) {
  const ref = React.useRef<HTMLElement | null>(null)
  const [shown, setShown] = React.useState(false)
  const [reduced, setReduced] = React.useState(false)

  // Detect prefers-reduced-motion once on mount.
  React.useEffect(() => {
    if (typeof window === "undefined") return
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)")
    setReduced(mq.matches)
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener?.("change", onChange)
    return () => mq.removeEventListener?.("change", onChange)
  }, [])

  React.useEffect(() => {
    if (reduced) {
      setShown(true)
      return
    }
    const node = ref.current
    if (!node) return
    if (typeof IntersectionObserver === "undefined") {
      setShown(true)
      return
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setShown(true)
            io.disconnect()
          }
        }
      },
      { threshold, rootMargin: "0px 0px -40px 0px" },
    )
    io.observe(node)
    return () => io.disconnect()
  }, [reduced, threshold])

  const Tag = as as React.ElementType
  return (
    <Tag
      ref={ref as React.Ref<HTMLDivElement>}
      data-reveal={shown ? "in" : "out"}
      className={cn(
        "transition-[opacity,transform] ease-[cubic-bezier(0.16,1,0.3,1)] duration-[700ms] will-change-transform",
        shown ? "opacity-100 translate-y-0" : "opacity-0 translate-y-3",
        className,
      )}
      style={{
        transitionDelay: shown ? `${delay}ms` : "0ms",
        ...style,
      }}
      {...rest}
    >
      {children}
    </Tag>
  )
}
