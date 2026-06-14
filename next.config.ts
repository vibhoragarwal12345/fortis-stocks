import type { NextConfig } from "next";

// Conservative security headers for a login-gated financial product. No CSP
// here — it needs per-route auditing against the inline styles/fonts and the
// Supabase origins, and a wrong CSP silently breaks the app; add it
// deliberately later. These five are safe and high-value.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-DNS-Prefetch-Control", value: "on" },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
];

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
