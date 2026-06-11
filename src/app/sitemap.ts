import type { MetadataRoute } from "next"

// Only the public surface: gateway, sign-in, and the legal pages.

export default function sitemap(): MetadataRoute.Sitemap {
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"
  const now = new Date()
  return [
    { url: `${siteUrl}/`, lastModified: now, priority: 1 },
    { url: `${siteUrl}/login`, lastModified: now, priority: 0.5 },
    { url: `${siteUrl}/privacy`, lastModified: now, priority: 0.3 },
    { url: `${siteUrl}/terms`, lastModified: now, priority: 0.3 },
    { url: `${siteUrl}/disclaimer`, lastModified: now, priority: 0.3 },
  ]
}
