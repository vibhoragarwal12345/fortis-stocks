"use client"

import * as React from "react"

import { useMediaQuery } from "@/hooks/use-media-query"

// Ambient cursor light. A soft cyan pool trails the pointer with a gentle
// lag (lerp at 0.12/frame), screen-blended over the dark surface so it
// reads as light, not paint. Mount once per page, ideally on marketing /
// gateway surfaces only — data-dense views don't need it.
//
// Guards:
//   - hover:none (touch) and prefers-reduced-motion render nothing.
//   - The rAF loop parks itself when the glow has converged and wakes on
//     the next pointer move, so an idle page costs zero frames.
//   - Fades in on first movement; pointer leaving the window fades it out.

export function CursorGlow() {
  const ref = React.useRef<HTMLDivElement>(null)
  const touch = useMediaQuery("(hover: none)")
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)")
  const enabled = !touch && !reduced

  React.useEffect(() => {
    if (!enabled) return
    const node = ref.current
    if (!node) return

    let targetX = 0
    let targetY = 0
    let x = 0
    let y = 0
    let raf = 0
    let parked = true

    const tick = () => {
      x += (targetX - x) * 0.12
      y += (targetY - y) * 0.12
      node.style.transform = `translate3d(${x}px, ${y}px, 0)`
      if (Math.abs(targetX - x) < 0.5 && Math.abs(targetY - y) < 0.5) {
        parked = true
        raf = 0
        return
      }
      raf = requestAnimationFrame(tick)
    }

    const onMove = (e: PointerEvent) => {
      targetX = e.clientX
      targetY = e.clientY
      if (node.dataset.active !== "true") {
        // First contact: snap to the pointer so the glow doesn't fly in
        // from the corner, then fade in.
        x = targetX
        y = targetY
        node.style.transform = `translate3d(${x}px, ${y}px, 0)`
        node.dataset.active = "true"
      }
      if (parked) {
        parked = false
        raf = requestAnimationFrame(tick)
      }
    }
    const onLeave = () => {
      node.dataset.active = "false"
    }

    window.addEventListener("pointermove", onMove, { passive: true })
    document.documentElement.addEventListener("pointerleave", onLeave)
    return () => {
      window.removeEventListener("pointermove", onMove)
      document.documentElement.removeEventListener("pointerleave", onLeave)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [enabled])

  if (!enabled) return null
  return <div ref={ref} aria-hidden className="cursor-glow" data-active="false" />
}
