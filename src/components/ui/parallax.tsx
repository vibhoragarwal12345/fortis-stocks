"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

// Scroll parallax layer — reserved for the landing hero backdrop.
//
// Translates its children at a fraction of the scroll distance so the
// background recedes behind the foreground. rAF-throttled passive scroll
// listener, transform-only (compositor-friendly), inert under
// prefers-reduced-motion.
//
//   <Parallax speed={0.3} className="absolute inset-0">
//     <AuroraField />
//   </Parallax>

export function Parallax({
  speed = 0.3,
  className,
  children,
}: {
  /** Fraction of scroll distance applied. 0 = fixed, 1 = normal flow. */
  speed?: number
  className?: string
  children: React.ReactNode
}) {
  const ref = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    if (typeof window === "undefined") return
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return

    const node = ref.current
    if (!node) return
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        node.style.transform = `translate3d(0, ${window.scrollY * speed}px, 0)`
        raf = 0
      })
    }
    window.addEventListener("scroll", onScroll, { passive: true })
    onScroll()
    return () => {
      window.removeEventListener("scroll", onScroll)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [speed])

  return (
    <div ref={ref} className={cn("will-change-transform", className)}>
      {children}
    </div>
  )
}
