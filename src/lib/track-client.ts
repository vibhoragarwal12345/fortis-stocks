/**
 * Client-side counterpart to src/lib/track.ts. Calls POST /api/track so
 * the SSR cookie session authenticates the insert; the server route
 * unpacks the event and writes through the same RLS path.
 *
 * Use from "use client" components only. For server components import
 * trackEvent from "@/lib/track" instead.
 */

import type { EventType, TrackPayload } from "./track"

export async function trackEventClient(
  type: EventType,
  data: TrackPayload = {}
): Promise<void> {
  try {
    await fetch("/api/track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_type: type, event_data: data }),
      // Keep this request alive past unmount so a click-then-navigate
      // event still lands. sendBeacon would also work; fetch+keepalive
      // is broader-supported in current evergreen browsers.
      keepalive: true,
    })
  } catch {
    // never crash the UI for a tracking failure
  }
}

// Convenience: re-export the union so client components only import
// from one path.
export type { EventType, TrackPayload }
