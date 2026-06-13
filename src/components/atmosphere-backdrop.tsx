import Image from "next/image"

import { cn } from "@/lib/utils"

// One iconic, instantly-recognizable Wall Street photograph, pushed far into the
// background: desaturated to B&W, darkened, blurred, low opacity, with a vignette
// keeping the centre (where content sits) calm. No motion — prefers-reduced-motion
// needs nothing. Glass cards above let it show through without losing legibility.
//
//   variant="atmosphere"  landing / login / signup — recognizable but quiet
//   variant="whisper"     data pages — barely perceptible behind solid content
export function AtmosphereBackdrop({
  src = "/floor/wallstreet.webp",
  variant = "atmosphere",
  position = "center 58%",
  className,
}: {
  src?: string
  variant?: "atmosphere" | "whisper"
  /** object-position for the photo; default keeps the Wall St sign in view. */
  position?: string
  className?: string
}) {
  const opacity = variant === "atmosphere" ? "opacity-[0.28]" : "opacity-[0.06]"
  const blur = variant === "atmosphere" ? "blur-[1px]" : "blur-[3px]"
  const bright = variant === "atmosphere" ? "brightness-[0.7]" : "brightness-[0.55]"
  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-background",
        className,
      )}
    >
      <Image
        src={src}
        alt=""
        fill
        sizes="100vw"
        priority={false}
        className={cn("object-cover grayscale", blur, bright, opacity)}
        style={{ objectPosition: position }}
      />
      {/* darkening + centre-calm vignette (lighter on atmosphere so the photo
          is perceptible; heavier on whisper to protect data) */}
      <div
        className="absolute inset-0"
        style={{
          background:
            variant === "atmosphere"
              ? "radial-gradient(120% 115% at 50% 38%, rgba(5,6,9,0.30) 0%, rgba(5,6,9,0.16) 50%, rgba(5,6,9,0.58) 100%)"
              : "radial-gradient(120% 115% at 50% 40%, rgba(5,6,9,0.55) 0%, rgba(5,6,9,0.34) 50%, rgba(5,6,9,0.78) 100%)",
        }}
      />
    </div>
  )
}
