"use client"

import * as React from "react"

// Media-query subscription via useSyncExternalStore — the React-blessed
// replacement for the matchMedia + setState-in-effect pattern. The server
// snapshot is always false (no media features on the server); the client
// resolves to the real value during hydration sync.
//
//   const reduced = useMediaQuery("(prefers-reduced-motion: reduce)")

export function useMediaQuery(query: string): boolean {
  const subscribe = React.useCallback(
    (onChange: () => void) => {
      const mq = window.matchMedia(query)
      mq.addEventListener?.("change", onChange)
      return () => mq.removeEventListener?.("change", onChange)
    },
    [query],
  )
  return React.useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  )
}
