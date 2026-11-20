import type { Metadata } from "next";
import "./globals.css";
import { PrefsProvider } from "@/lib/prefs";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "OriginPulse",
  description: "Green coffee price intelligence & Atlanta landed-cost tracker",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen font-sans antialiased">
        <PrefsProvider>
          <Nav />
          <main className="mx-auto max-w-[1400px] px-4 pb-16 pt-6 sm:px-6">{children}</main>
        </PrefsProvider>
      </body>
    </html>
  );
}
