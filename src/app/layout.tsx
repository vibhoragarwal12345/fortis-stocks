import type { Metadata } from "next";
import { Inter, Geist_Mono } from "next/font/google";
import "./globals.css";

// Inter for the full type scale. Loaded with axes for weights 400 + 600 (the
// only two we use anywhere -- premium = restraint, including font weights).
// Geist_Mono kept for tabular data + tickers.
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Fortis Stock Intelligence",
  description: "Institutional-grade stock intelligence for The Fortis Agency.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col font-sans text-foreground bg-background">
        {children}
      </body>
    </html>
  );
}
