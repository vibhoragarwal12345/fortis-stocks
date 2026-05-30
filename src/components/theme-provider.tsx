"use client"

import * as React from "react"
import { ThemeProvider as NextThemesProvider } from "next-themes"

// Thin wrapper so we can pass server-component children through without
// dragging the "use client" boundary into the root layout itself.
export function ThemeProvider({
  children,
  ...props
}: React.ComponentProps<typeof NextThemesProvider>) {
  return <NextThemesProvider {...props}>{children}</NextThemesProvider>
}
