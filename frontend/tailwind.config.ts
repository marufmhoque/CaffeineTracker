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
        accent: v("series-1"),
        good: v("good-text"),
        bad: v("critical"),
        warn: v("warning"),
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', '"Segoe UI"', "sans-serif"],
      },
    },
  },
  plugins: [],
} satisfies Config;
