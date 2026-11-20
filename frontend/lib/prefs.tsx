"use client";

import { createContext, useContext, useEffect, useState } from "react";

export type Unit = "lb" | "kg";
const LB_PER_KG = 2.2046226218487757;

type Prefs = {
  unit: Unit;
  setUnit: (u: Unit) => void;
  theme: "system" | "light" | "dark";
  setTheme: (t: "system" | "light" | "dark") => void;
};

const Ctx = createContext<Prefs>({
  unit: "lb",
  setUnit: () => {},
  theme: "system",
  setTheme: () => {},
});

function read<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T) || fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {}
}

export function PrefsProvider({ children }: { children: React.ReactNode }) {
  const [unit, setUnitState] = useState<Unit>("lb");
  const [theme, setThemeState] = useState<"system" | "light" | "dark">("system");

  useEffect(() => {
    setUnitState(read<Unit>("op.unit", "lb"));
    setThemeState(read("op.theme", "system"));
  }, []);

  useEffect(() => {
    const el = document.documentElement;
    if (theme === "system") el.removeAttribute("data-theme");
    else el.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <Ctx.Provider
      value={{
        unit,
        setUnit: (u) => {
          setUnitState(u);
          write("op.unit", u);
        },
        theme,
        setTheme: (t) => {
          setThemeState(t);
          write("op.theme", t);
        },
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export const usePrefs = () => useContext(Ctx);

/** Convert a USD/lb value to the selected unit. */
export function toUnit(usdLb: number | null | undefined, unit: Unit): number | null {
  if (usdLb === null || usdLb === undefined) return null;
  return unit === "kg" ? usdLb * LB_PER_KG : usdLb;
}

export function useMoney() {
  const { unit } = usePrefs();
  return {
    unit,
    suffix: `/${unit}`,
    conv: (v: number | null | undefined) => toUnit(v, unit),
    fmt: (v: number | null | undefined, digits = 2) => {
      const x = toUnit(v, unit);
      return x === null ? "—" : `$${x.toFixed(digits)}`;
    },
    fmtSigned: (v: number | null | undefined, digits = 2) => {
      const x = toUnit(v, unit);
      if (x === null) return "—";
      return `${x >= 0 ? "+" : "−"}$${Math.abs(x).toFixed(digits)}`;
    },
  };
}

export function fmtPct(v: number | null | undefined, digits = 1) {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}%`;
}

export const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
