"use client"

import * as React from "react"
import * as THREE from "three"
import { useTheme } from "next-themes"

import { cn } from "@/lib/utils"

// ─────────────────────────────────────────────────────────────────────────────
//  Dotted Surface
//  ---------------------------------------------------------------------------
//  Animated three.js dot field. Sized to its CONTAINER (not the viewport) so it
//  can sit inside any positioned parent. Apple-tier subtlety: low opacity, soft
//  gray tones, paused when the tab is hidden, downgraded on mobile, disabled
//  entirely when the user prefers reduced motion.
//
//  Usage (it must live inside a positioned parent):
//
//      <div className="relative">
//        <DottedSurface />
//        <main className="relative">{children}</main>
//      </div>
//
//  Acceptance contract (every one of these is enforced below):
//    1. Visibility pause   -- cancelAnimationFrame on document.hidden=true,
//                             resumes on visibilitychange back to visible.
//    2. Mobile downgrade   -- AMOUNT_X/Y drop to 20/30 below 768px viewports.
//    3. Reduced motion     -- prefers-reduced-motion: reduce -> render nothing.
//    4. Subtlety           -- opacity 0.30; light=rgb(60,60,60), dark=rgb(120,120,120).
//    5. Positioning        -- absolute inset-0 by default; className wins.
//    6. Cleanup            -- frame cancelled, geometry/material/renderer
//                             disposed, canvas removed, WEBGL_lose_context fired
//                             so the GPU context is returned promptly.
// ─────────────────────────────────────────────────────────────────────────────

type DottedSurfaceProps = Omit<React.ComponentProps<"div">, "ref">

export function DottedSurface({ className, ...props }: DottedSurfaceProps) {
  const { resolvedTheme } = useTheme()
  const containerRef = React.useRef<HTMLDivElement>(null)

  // Reduced-motion detection is JS-side because we don't want to render the
  // <div> at all if the user wants no motion -- avoids reserving the layer.
  const [shouldRender, setShouldRender] = React.useState(true)
  React.useEffect(() => {
    if (typeof window === "undefined") return
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)")
    setShouldRender(!mq.matches)
    const onChange = () => setShouldRender(!mq.matches)
    mq.addEventListener?.("change", onChange)
    return () => mq.removeEventListener?.("change", onChange)
  }, [])

  React.useEffect(() => {
    if (!shouldRender) return
    const container = containerRef.current
    if (!container) return

    // ── 2. Mobile downgrade ──────────────────────────────────────────────
    const isSmall =
      typeof window !== "undefined" && window.innerWidth < 768
    const AMOUNT_X = isSmall ? 20 : 40
    const AMOUNT_Y = isSmall ? 30 : 60
    const SEPARATION = 150

    // ── 4. Subtle palette ────────────────────────────────────────────────
    const isDark = resolvedTheme === "dark"
    const COLOR_R = isDark ? 120 : 60
    const COLOR_G = isDark ? 120 : 60
    const COLOR_B = isDark ? 120 : 60

    // ── Scene + camera ────────────────────────────────────────────────────
    const rect = container.getBoundingClientRect()
    const width = Math.max(1, rect.width || window.innerWidth)
    const height = Math.max(1, rect.height || window.innerHeight)

    const scene = new THREE.Scene()
    scene.fog = new THREE.Fog(0xffffff, 2000, 10000)

    const camera = new THREE.PerspectiveCamera(60, width / height, 1, 10000)
    camera.position.set(0, 355, 1220)

    const renderer = new THREE.WebGLRenderer({
      alpha: true,
      antialias: true,
      // powerPreference low-power is a hint to the GPU scheduler that this
      // is decoration; saves battery on integrated graphics laptops.
      powerPreference: "low-power",
    })
    // Cap DPR at 1.5 -- retina at devicePixelRatio=3 is wasteful for a
    // backdrop and is one of the biggest CPU/GPU wins on retina mobile.
    renderer.setPixelRatio(
      Math.min(typeof window !== "undefined" ? window.devicePixelRatio : 1, 1.5),
    )
    renderer.setSize(width, height)
    renderer.setClearColor(0x000000, 0)
    const canvas = renderer.domElement
    canvas.style.display = "block"
    canvas.style.width = "100%"
    canvas.style.height = "100%"
    container.appendChild(canvas)

    // ── Geometry ──────────────────────────────────────────────────────────
    const positions = new Float32Array(AMOUNT_X * AMOUNT_Y * 3)
    const colors = new Float32Array(AMOUNT_X * AMOUNT_Y * 3)
    {
      let i = 0
      for (let ix = 0; ix < AMOUNT_X; ix++) {
        for (let iy = 0; iy < AMOUNT_Y; iy++) {
          positions[i * 3 + 0] = ix * SEPARATION - (AMOUNT_X * SEPARATION) / 2
          positions[i * 3 + 1] = 0
          positions[i * 3 + 2] = iy * SEPARATION - (AMOUNT_Y * SEPARATION) / 2
          colors[i * 3 + 0] = COLOR_R / 255
          colors[i * 3 + 1] = COLOR_G / 255
          colors[i * 3 + 2] = COLOR_B / 255
          i++
        }
      }
    }
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3))
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3))

    const material = new THREE.PointsMaterial({
      size: isSmall ? 6 : 8,
      vertexColors: true,
      transparent: true,
      opacity: 0.3, // ── 4. Subtle: never compete with foreground text.
      sizeAttenuation: true,
      depthWrite: false,
    })

    const points = new THREE.Points(geometry, material)
    scene.add(points)

    // ── Animation loop with visibility pause ─────────────────────────────
    let frameId = 0
    let count = 0
    let running = true

    const renderOnce = () => {
      const posAttr = geometry.attributes.position as THREE.BufferAttribute
      const arr = posAttr.array as Float32Array
      let i = 0
      for (let ix = 0; ix < AMOUNT_X; ix++) {
        for (let iy = 0; iy < AMOUNT_Y; iy++) {
          arr[i * 3 + 1] =
            Math.sin((ix + count) * 0.3) * 50 +
            Math.sin((iy + count) * 0.5) * 50
          i++
        }
      }
      posAttr.needsUpdate = true
      renderer.render(scene, camera)
    }

    const tick = () => {
      if (!running) return
      renderOnce()
      count += 0.1
      frameId = requestAnimationFrame(tick)
    }

    // ── 1. Pause when tab hidden ─────────────────────────────────────────
    const onVisibility = () => {
      if (document.hidden) {
        running = false
        if (frameId) cancelAnimationFrame(frameId)
        frameId = 0
      } else if (!running) {
        running = true
        frameId = requestAnimationFrame(tick)
      }
    }
    document.addEventListener("visibilitychange", onVisibility)

    // ── Resize observer (container-scoped, not window-scoped) ────────────
    const onResize = () => {
      const r = container.getBoundingClientRect()
      const w = Math.max(1, r.width)
      const h = Math.max(1, r.height)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h, false)
    }
    const ro = new ResizeObserver(onResize)
    ro.observe(container)

    // Render the first frame immediately so the static look is correct
    // before the first rAF lands.
    renderOnce()
    frameId = requestAnimationFrame(tick)

    // ── 6. Hard cleanup ──────────────────────────────────────────────────
    return () => {
      running = false
      if (frameId) cancelAnimationFrame(frameId)
      document.removeEventListener("visibilitychange", onVisibility)
      ro.disconnect()

      scene.remove(points)
      geometry.dispose()
      material.dispose()

      // Force the GPU context to be released ASAP -- otherwise Chrome will
      // hold the WebGL context until GC runs, which means navigating
      // back-and-forth quickly can hit the "Too many active WebGL contexts"
      // warning. WEBGL_lose_context fixes that.
      const gl = renderer.getContext()
      const lose = gl?.getExtension("WEBGL_lose_context")
      lose?.loseContext()
      renderer.dispose()

      if (canvas.parentNode === container) {
        container.removeChild(canvas)
      }
    }
  }, [resolvedTheme, shouldRender])

  if (!shouldRender) return null

  return (
    <div
      ref={containerRef}
      aria-hidden
      className={cn("pointer-events-none absolute inset-0 -z-10", className)}
      {...props}
    />
  )
}
