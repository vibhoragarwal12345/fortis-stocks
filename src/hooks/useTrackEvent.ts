"use client"

import { useEffect, useRef } from "react"

import { trackEventClient, type EventType, type TrackPayload } from "@/lib/track-client"

/**
 * Fire a tracking event exactly once when the component mounts. The 5-second
 * dedup bucket on the server makes React StrictMode double-mounts a no-op,
 * but we also keep a ref guard so the network request itself doesn't fire
 * twice in dev.
 */
export function useTrackEvent(type: EventType, data: TrackPayload = {}): void {
  const fired = useRef(false)
  useEffect(() => {
    if (fired.current) return
    fired.current = true
    void trackEventClient(type, data)
    // We intentionally exclude `data` from deps -- a payload object literal
    // would re-fire the effect on every render. Callers who need a re-fire
    // on payload change should call trackEventClient directly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type])
}
