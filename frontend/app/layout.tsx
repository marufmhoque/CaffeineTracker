import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { PrefsProvider } from "@/lib/prefs";
import Nav from "@/components/Nav";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "OriginPulse",
  description: "Green coffee price intelligence & Atlanta landed-cost tracker",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${sans.variable} ${mono.variable}`}>
      <body className="min-h-screen font-sans antialiased">
        <PrefsProvider>
          <Nav />
          <main className="mx-auto max-w-[1400px] px-4 pb-16 pt-8 sm:px-6">{children}</main>
        </PrefsProvider>
      </body>
    </html>
  );
}
