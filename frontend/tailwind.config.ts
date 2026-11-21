import type { Config } from "tailwindcss";

const v = (name: string) => `var(--${name})`;

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        page: v("page"),
        surface: v("surface-1"),
        surface2: v("surface-2"),
        ink: v("text-primary"),
        ink2: v("text-secondary"),
        muted: v("text-muted"),
        grid: v("grid"),
        axis: v("axis"),
        border: v("border"),
        accent: v("accent"),
        "accent-strong": v("accent-strong"),
        "accent-soft": v("accent-soft"),
        pos: v("pos-text"),
        "pos-bg": v("pos-bg"),
        neg: v("neg-text"),
        "neg-bg": v("neg-bg"),
        warn: v("warn-text"),
        "warn-bg": v("warn-bg"),
        good: v("good-text"),
        bad: v("neg-text"),
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        pop: v("shadow-pop"),
      },
    },
  },
  plugins: [],
} satisfies Config;
