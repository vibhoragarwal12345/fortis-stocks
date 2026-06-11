"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { cn } from "@/lib/utils"

// Dashboard nav link with active state. `exact` for the index route
// (/dashboard matches everything otherwise), prefix matching for sections
// so detail pages keep their tab lit.

export function NavLink({
  href,
  exact = false,
  className,
  children,
}: {
  href: string
  exact?: boolean
  className?: string
  children: React.ReactNode
}) {
  const pathname = usePathname()
  const active = exact ? pathname === href : pathname.startsWith(href)

  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "whitespace-nowrap rounded-md px-3 py-1.5 transition-premium",
        active
          ? "bg-secondary/70 font-medium text-foreground"
          : "text-muted-foreground hover:text-foreground",
        className,
      )}
    >
      {children}
    </Link>
  )
}
