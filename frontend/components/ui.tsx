"use client";

import { fmtPct, useMoney } from "@/lib/prefs";

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-[26px] font-semibold tracking-[-0.02em]">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-ink2">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

/**
 * Dual price: bold primary in the selected unit, muted secondary in the other unit.
 * `layout="stack"` puts the secondary underneath (KPI cards, table cells); "inline" puts it alongside.
 */
export function Price({
  usdLb,
  digits = 2,
  size = "md",
  layout = "inline",
  signed = false,
  className = "",
}: {
  usdLb: number | null | undefined;
  digits?: number;
  size?: "sm" | "md" | "lg" | "xl";
  layout?: "inline" | "stack";
  signed?: boolean;
  className?: string;
}) {
  const m = useMoney();
  if (usdLb == null) return <span className={`mono text-muted ${className}`}>—</span>;
  const d = m.dual(usdLb, digits, signed);
  const main = { sm: "text-sm", md: "text-sm", lg: "text-xl", xl: "text-[28px] leading-none" }[size];
  const sec = { sm: "text-[11px]", md: "text-xs", lg: "text-xs", xl: "text-sm" }[size];
  return (
    <span className={`mono inline-flex ${layout === "stack" ? "flex-col items-end gap-0.5" : "items-baseline gap-1.5"} ${className}`}>
      <span className={`${main} font-semibold tracking-tight text-ink`}>{d.primary}</span>
      <span className={`${sec} font-normal text-muted`}>{layout === "inline" ? `(${d.secondary})` : d.secondary}</span>
    </span>
  );
}

export function StatTile({
  label,
  usdLb,
  value,
  digits = 2,
  sub,
  delta,
  deltaLabel,
  badge,
}: {
  label: React.ReactNode;
  usdLb?: number | null;
  value?: React.ReactNode;
  digits?: number;
  sub?: React.ReactNode;
  delta?: number | null;
  deltaLabel?: string;
  badge?: React.ReactNode;
}) {
  return (
    <div className="card card-hover">
      <div className="flex items-start justify-between gap-2">
        <div className="eyebrow">{label}</div>
        {badge}
      </div>
      <div className="mt-3">
        {value !== undefined ? (
          <div className="mono text-[26px] font-semibold leading-none tracking-tight">{value}</div>
        ) : (
          <Price usdLb={usdLb} digits={digits} size="xl" layout="stack" className="!items-start" />
        )}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink2">
        {delta !== undefined && <Delta value={delta} label={deltaLabel} />}
        {sub}
      </div>
    </div>
  );
}

/** Change pill: positive emerald, negative rose, flat neutral. Arrow + sign keep it readable without color. */
export function Delta({ value, label, digits = 1 }: { value: number | null | undefined; label?: string; digits?: number }) {
  if (value === null || value === undefined) return <span className="mono text-xs text-muted">—</span>;
  const flat = Math.abs(value) < 0.05;
  const cls = flat ? "bg-surface2 text-ink2" : value > 0 ? "bg-pos-bg text-pos" : "bg-neg-bg text-neg";
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`mono inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-xs font-medium ${cls}`}>
        <span aria-hidden className="text-[9px]">{flat ? "■" : value > 0 ? "▲" : "▼"}</span>
        {fmtPct(value, digits)}
      </span>
      {label && <span className="text-xs text-muted">{label}</span>}
    </span>
  );
}

/** Small metadata chip. */
export function Badge({
  children,
  tone = "neutral",
  title,
}: {
  children: React.ReactNode;
  tone?: "neutral" | "accent" | "pos" | "neg" | "warn";
  title?: string;
}) {
  const cls = {
    neutral: "bg-surface2 text-ink2 border-border",
    accent: "bg-accent-soft text-accent-strong border-[var(--accent-ring)]",
    pos: "bg-pos-bg text-pos border-transparent",
    neg: "bg-neg-bg text-neg border-transparent",
    warn: "bg-warn-bg text-warn border-transparent",
  }[tone];
  return (
    <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>
      {children}
    </span>
  );
}

const RISK: Record<string, { tone: "pos" | "warn" | "neg"; icon: string }> = {
  Low: { tone: "pos", icon: "●" },
  Moderate: { tone: "warn", icon: "▲" },
  Critical: { tone: "neg", icon: "◆" },
};

export function RiskBadge({ risk, prefix = "Weather risk" }: { risk: string | null | undefined; prefix?: string }) {
  if (!risk) return <Badge>{prefix}: no data</Badge>;
  const r = RISK[risk] ?? RISK.Low;
  return (
    <Badge tone={r.tone}>
      <span aria-hidden className="text-[8px]">{r.icon}</span>
      {prefix ? `${prefix}: ` : ""}
      {risk}
    </Badge>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-[var(--border-strong)] p-8 text-center">
      <div className="text-sm font-medium">{title}</div>
      {children && <div className="mx-auto mt-1 max-w-xl text-sm text-ink2">{children}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div className="mb-4 rounded-lg border border-border bg-neg-bg px-3 py-2 text-sm">
      <span className="font-medium text-neg">Couldn’t load data:</span> <span className="text-ink2">{error}</span>
      <span className="text-muted"> — is the API running on :8000?</span>
    </div>
  );
}

const HARVEST_TONE: Record<string, "pos" | "accent" | "warn" | "neutral"> = {
  "In Harvest": "pos",
  "Fly Crop": "warn",
  "Main Crop Shipping": "accent",
  "Off-Season": "neutral",
};

export function HarvestBadge({ status }: { status: string }) {
  return <Badge tone={HARVEST_TONE[status] ?? "neutral"}>{status}</Badge>;
}

/** Minimal pill toggle group. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  size = "md",
}: {
  value: T;
  options: { value: T; label: string; disabled?: boolean; title?: string }[];
  onChange: (v: T) => void;
  label: string;
  size?: "sm" | "md";
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-full border border-border bg-surface2 p-0.5">
      {options.map((o) => {
        const on = value === o.value;
        return (
          <button
            key={o.value}
            aria-pressed={on}
            disabled={o.disabled}
            title={o.title}
            onClick={() => onChange(o.value)}
            className={`rounded-full ${size === "sm" ? "px-2.5 py-0.5 text-xs" : "px-3 py-1 text-[13px]"} font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
              on ? "bg-surface text-ink shadow-[0_1px_2px_rgba(0,0,0,0.08)] ring-1 ring-border" : "text-muted hover:text-ink"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export type Species = "All" | "Arabica" | "Robusta";

export function SpeciesToggle({ value, onChange, size }: { value: Species; onChange: (v: Species) => void; size?: "sm" | "md" }) {
  return (
    <Segmented
      label="Species"
      value={value}
      onChange={onChange}
      size={size}
      options={[
        { value: "Arabica", label: "Arabica" },
        { value: "Robusta", label: "Robusta" },
        { value: "All", label: "All" },
      ]}
    />
  );
}

export function timeAgo(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
