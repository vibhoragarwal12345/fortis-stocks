import type { MetadataRoute } from "next"

// The product is login-only; crawlers get the gateway and the legal pages,
// nothing behind auth and no internal tooling.

export default function robots(): MetadataRoute.Robots {
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [
        "/dashboard",
        "/admin",
        "/account",
        "/api",
        "/auth",
        "/design-system",
      ],
    },
    sitemap: `${siteUrl}/sitemap.xml`,
  }
}
