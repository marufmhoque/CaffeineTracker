"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { usePrefs } from "@/lib/prefs";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/origins", label: "Origin Matrix" },
  { href: "/offers", label: "Spot Offers" },
  { href: "/trends", label: "Trends" },
  { href: "/calculator", label: "Landed Cost" },
  { href: "/alerts", label: "Alerts" },
  { href: "/data", label: "Data Sources" },
];

export default function Nav() {
  const path = usePathname();
  const { unit, setUnit, theme, setTheme } = usePrefs();
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api<{ unread: number }>("/alerts?unread=true&limit=1")
        .then((d) => alive && setUnread(d.unread))
        .catch(() => {});
    tick();
    const t = setInterval(tick, 60_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [path]);

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-[var(--surface-1)]/90 backdrop-blur-md">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
        <Link href="/" className="flex items-center gap-2">
          <span aria-hidden className="grid h-6 w-6 place-items-center rounded-md bg-[var(--accent)] text-[13px] font-bold text-white">
            O
          </span>
          <span className="text-[15px] font-semibold tracking-tight">OriginPulse</span>
          <span className="hidden text-xs text-muted xl:inline">Green coffee · Atlanta landed cost</span>
        </Link>
        <nav className="order-3 -mx-1 flex w-full gap-0.5 overflow-x-auto lg:order-none lg:w-auto">
          {LINKS.map((l) => {
            const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                aria-current={active ? "page" : undefined}
                className={`relative whitespace-nowrap rounded-full px-3 py-1.5 text-[13px] font-medium transition-colors ${
                  active ? "bg-surface2 text-ink" : "text-muted hover:text-ink"
                }`}
              >
                {l.label}
                {l.href === "/alerts" && unread > 0 && (
                  <span className="mono ml-1.5 rounded-full bg-[var(--accent-strong)] px-1.5 py-0.5 text-[10px] font-semibold text-white">
                    {unread}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <div
            role="group"
            aria-label="Primary price unit"
            title="Primary unit — the other unit is shown alongside"
            className="inline-flex rounded-full border border-border bg-surface2 p-0.5 text-[13px]"
          >
            {(["lb", "kg"] as const).map((u) => (
              <button
                key={u}
                onClick={() => setUnit(u)}
                aria-pressed={unit === u}
                className={`mono rounded-full px-2.5 py-0.5 font-medium ${
                  unit === u ? "bg-surface text-ink ring-1 ring-border" : "text-muted hover:text-ink"
                }`}
              >
                $/{u}
              </button>
            ))}
          </div>
          <select
            aria-label="Theme"
            className="input rounded-full py-1 text-[13px]"
            value={theme}
            onChange={(e) => setTheme(e.target.value as "system" | "light" | "dark")}
          >
            <option value="system">Auto</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </div>
      </div>
    </header>
  );
}
