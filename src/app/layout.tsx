import type { Metadata } from "next";
import { DM_Serif_Display, Fraunces, Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

import { CursorGlow } from "@/components/ui/cursor-glow";
import { ThemeProvider } from "@/components/theme-provider";

// Split type system:
//   Fraunces (display/titles) · DM Serif Display (body copy) ·
//   Geist (UI chrome/labels) · Geist Mono (data, tabular numerals).
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

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  display: "swap",
});

const dmSerif = DM_Serif_Display({
  variable: "--font-dmserif",
  weight: "400",
  subsets: ["latin"],
  display: "swap",
});

import { SITE_URL } from "@/lib/site"

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
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
    url: SITE_URL,
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
      className={`${geistSans.variable} ${geistMono.variable} ${fraunces.variable} ${dmSerif.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col font-sans text-foreground bg-background">
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem={false}
          disableTransitionOnChange
        >
          {/* the small light behind the pointer — global, all pages */}
          <CursorGlow />
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
