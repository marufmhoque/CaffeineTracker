"use client";

import { fmtPct } from "@/lib/prefs";

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-ink2">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

export function StatTile({
  label,
  value,
  sub,
  delta,
  deltaLabel,
  invertDelta,
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
  delta?: number | null;
  deltaLabel?: string;
  invertDelta?: boolean;
}) {
  return (
    <div className="card">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-3xl font-semibold tracking-tight">{value}</div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink2">
        {delta !== undefined && <Delta value={delta} invert={invertDelta} label={deltaLabel} />}
        {sub}
      </div>
    </div>
  );
}

/** Price change: for a buyer, falling prices are good (green ▼); rising are bad (red ▲). */
export function Delta({ value, invert = false, label }: { value: number | null | undefined; invert?: boolean; label?: string }) {
  if (value === null || value === undefined) return <span className="text-muted">—</span>;
  const up = value > 0;
  const good = invert ? up : !up;
  const flat = Math.abs(value) < 0.05;
  return (
    <span
      className={`inline-flex items-center gap-0.5 font-medium ${flat ? "text-ink2" : good ? "text-good" : "text-bad"}`}
      title={label}
    >
      <span aria-hidden>{flat ? "■" : up ? "▲" : "▼"}</span>
      {fmtPct(value)}
      {label && <span className="ml-1 font-normal text-muted">{label}</span>}
    </span>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-border p-8 text-center">
      <div className="font-medium">{title}</div>
      {children && <div className="mx-auto mt-1 max-w-xl text-sm text-ink2">{children}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div className="mb-4 rounded-lg border border-border bg-surface px-3 py-2 text-sm">
      <span className="font-medium text-bad">⚠ Couldn’t load data:</span> <span className="text-ink2">{error}</span>
      <span className="text-muted"> — is the API running on :8000?</span>
    </div>
  );
}

const HARVEST_STYLE: Record<string, string> = {
  "In Harvest": "bg-[var(--series-3)] text-white",
  "Fly Crop": "bg-[var(--series-4)] text-[#0b0b0b]",
  "Main Crop Shipping": "bg-[var(--series-1)] text-white",
  "Off-Season": "bg-surface2 text-ink2",
};

export function HarvestBadge({ status }: { status: string }) {
  return (
    <span className={`inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${HARVEST_STYLE[status] ?? ""}`}>
      {status}
    </span>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="flex rounded-lg border border-border bg-surface p-0.5 text-sm">
      {options.map((o) => (
        <button
          key={o.value}
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={`rounded-md px-2.5 py-1 ${value === o.value ? "bg-surface2 font-medium" : "text-ink2"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function timeAgo(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
