import type { MetadataRoute } from "next"

import { SITE_URL } from "@/lib/site"

// Only the public surface: gateway, sign-in, and the legal pages.

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date()
  return [
    { url: `${SITE_URL}/`, lastModified: now, priority: 1 },
    { url: `${SITE_URL}/login`, lastModified: now, priority: 0.5 },
    { url: `${SITE_URL}/privacy`, lastModified: now, priority: 0.3 },
    { url: `${SITE_URL}/terms`, lastModified: now, priority: 0.3 },
    { url: `${SITE_URL}/disclaimer`, lastModified: now, priority: 0.3 },
  ]
}
