import type { MetadataRoute } from "next"

import { SITE_URL } from "@/lib/site"

// The product is login-only; crawlers get the gateway and the legal pages,
// nothing behind auth.

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/dashboard", "/admin", "/account", "/api", "/auth"],
    },
    sitemap: `${SITE_URL}/sitemap.xml`,
  }
}
