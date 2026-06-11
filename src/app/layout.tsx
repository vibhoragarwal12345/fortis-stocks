import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

import { ThemeProvider } from "@/components/theme-provider";

// Geist Sans carries the entire type scale (variable font — one file, all
// weights). Geist Mono renders every metric, price, and ticker with
// tabular numerals.
const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

// Set NEXT_PUBLIC_SITE_URL in production so OG/sitemap URLs are absolute.
const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "Fortis Stock Intelligence",
    template: "%s — Fortis",
  },
  description:
    "Institutional research for wealth advisors. The full liquid US market scanned through every trading day — every claim traced to its source.",
  openGraph: {
    title: "Fortis Stock Intelligence",
    description:
      "Three thousand stocks. Thirty convictions. Institutional research for wealth advisors — every claim traced to its source.",
    url: siteUrl,
    siteName: "Fortis Stock Intelligence",
    type: "website",
  },
  twitter: {
    card: "summary",
    title: "Fortis Stock Intelligence",
    description:
      "Three thousand stocks. Thirty convictions. Institutional research for wealth advisors.",
  },
}

export const viewport = {
  themeColor: "#0a0c10",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col font-sans text-foreground bg-background">
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem={false}
          disableTransitionOnChange
        >
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
