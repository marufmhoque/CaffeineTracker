"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  ComposedChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { HistoryPoint, TradePoint } from "@/lib/api";
import { useMoney } from "@/lib/prefs";

const AXIS = { stroke: "var(--axis)", tick: { fill: "var(--text-muted)", fontSize: 11 }, tickLine: false };
const fmtDate = (d: string) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
const signed = (v: number) => `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(2)}`;
const fmtMonth = (d: string) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { month: "short", year: "2-digit" });

function TipBox({ title, rows }: { title: string; rows: { label: string; value: string; color?: string }[] }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-2.5 text-sm shadow-lg">
      <div className="mb-1 text-xs text-muted">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-4">
          <span className="inline-flex items-center gap-1.5 text-ink2">
            {r.color && <span className="inline-block h-0.5 w-3 rounded" style={{ background: r.color }} />}
            {r.label}
          </span>
          <span className="num font-medium">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; dot?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink2">
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5">
          {i.dot ? (
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: i.color }} />
          ) : (
            <span className="inline-block h-0.5 w-4 rounded" style={{ background: i.color }} />
          )}
          {i.label}
        </span>
      ))}
    </div>
  );
}

/** Spot average vs ICE "C" (same unit, one axis). */
export function PriceChart({ points, label }: { points: HistoryPoint[]; label: string }) {
  const m = useMoney();
  const data = points.map((p) => ({ date: p.date, spot: m.conv(p.spot_avg_usd_lb), c: m.conv(p.ice_c_usd_lb), n: p.spot_n }));
  // Sparse spot history (e.g. scraping just started): show markers so single observations are visible.
  const sparse = data.filter((d) => d.spot !== null).length < 10;
  return (
    <div className="h-72 w-full">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="date" tickFormatter={fmtDate} minTickGap={40} {...AXIS} />
          <YAxis tickFormatter={(v) => `$${v.toFixed(2)}`} width={56} domain={["auto", "auto"]} {...AXIS} axisLine={false} />
          <Tooltip
            cursor={{ stroke: "var(--axis)" }}
            content={({ active, payload, label: l }) =>
              active && payload?.length ? (
                <TipBox
                  title={fmtDate(String(l))}
                  rows={[
                    { label: `${label} spot avg`, value: payload[0]?.payload.spot != null ? `$${payload[0].payload.spot.toFixed(2)} (${payload[0].payload.n})` : "—", color: "var(--series-1)" },
                    { label: "ICE “C”", value: payload[0]?.payload.c != null ? `$${payload[0].payload.c.toFixed(4)}` : "—", color: "var(--series-2)" },
                  ]}
                />
              ) : null
            }
          />
          <Line
            type="monotone"
            dataKey="spot"
            stroke="var(--series-1)"
            strokeWidth={2}
            dot={sparse ? { r: 4, strokeWidth: 2, stroke: "var(--surface-1)", fill: "var(--series-1)" } : false}
            connectNulls
            isAnimationActive={false}
          />
          <Line type="monotone" dataKey="c" stroke="var(--series-2)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Origin differential over ICE "C" — a single series around a zero baseline. */
export function DiffChart({ points }: { points: HistoryPoint[] }) {
  const m = useMoney();
  const data = points.filter((p) => p.diff_usd_lb !== null).map((p) => ({ date: p.date, diff: m.conv(p.diff_usd_lb) }));
  return (
    <div className="h-56 w-full">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="date" tickFormatter={fmtDate} minTickGap={40} {...AXIS} />
          <YAxis tickFormatter={(v) => `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(2)}`} width={60} {...AXIS} axisLine={false} />
          <ReferenceLine y={0} stroke="var(--axis)" />
          <Tooltip
            cursor={{ stroke: "var(--axis)" }}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <TipBox
                  title={fmtDate(String(label))}
                  rows={[{ label: "Differential", value: signed(payload[0].payload.diff), color: "var(--series-1)" }]}
                />
              ) : null
            }
          />
          <Line type="monotone" dataKey="diff" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

const REPORTER_COLOR: Record<string, string> = { USA: "var(--series-1)", DEU: "var(--series-2)", JPN: "var(--series-3)" };

/** Customs import unit values per reporter market (monthly points; annual shown as steps). */
export function TradeChart({ trade }: { trade: TradePoint[] }) {
  const m = useMoney();
  const reporters = Array.from(new Set(trade.map((t) => t.reporter))).slice(0, 3);
  const monthly = trade.filter((t) => t.freq === "M");
  const periods = Array.from(new Set(monthly.map((t) => t.period))).sort();
  const data = periods.map((p) => {
    const row: Record<string, number | string | null> = { period: p };
    reporters.forEach((r) => {
      const hit = monthly.find((t) => t.period === p && t.reporter === r);
      row[r] = hit ? m.conv(hit.unit_usd_lb) : null;
    });
    return row;
  });
  if (!data.length) return null;
  return (
    <>
      <Legend items={reporters.map((r) => ({ label: `Imports into ${r}`, color: REPORTER_COLOR[r] ?? "var(--series-4)", dot: true }))} />
      <div className="mt-2 h-56 w-full">
        <ResponsiveContainer>
          <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="period" tickFormatter={fmtMonth} minTickGap={30} {...AXIS} />
            <YAxis tickFormatter={(v) => `$${v.toFixed(2)}`} width={56} domain={["auto", "auto"]} {...AXIS} axisLine={false} />
            <Tooltip
              cursor={{ stroke: "var(--axis)" }}
              content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <TipBox
                    title={fmtMonth(String(label))}
                    rows={reporters.map((r) => ({
                      label: r,
                      value: payload[0].payload[r] != null ? `$${(payload[0].payload[r] as number).toFixed(2)}` : "—",
                      color: REPORTER_COLOR[r],
                    }))}
                  />
                ) : null
              }
            />
            {reporters.map((r) => (
              <Line
                key={r}
                dataKey={r}
                stroke={REPORTER_COLOR[r] ?? "var(--series-4)"}
                strokeWidth={2}
                dot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-1)", fill: REPORTER_COLOR[r] ?? "var(--series-4)" }}
                connectNulls
                isAnimationActive={false}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

/** ICE "C" one-year sparkline for the overview. */
export function IceChart({ data }: { data: { date: string; ice_arabica_usd_lb: number | null }[] }) {
  const m = useMoney();
  const rows = data.filter((d) => d.ice_arabica_usd_lb !== null).map((d) => ({ date: d.date, c: m.conv(d.ice_arabica_usd_lb) }));
  return (
    <div className="h-48 w-full">
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="date" tickFormatter={fmtMonth} minTickGap={50} {...AXIS} />
          <YAxis tickFormatter={(v) => `$${v.toFixed(2)}`} width={52} domain={["auto", "auto"]} {...AXIS} axisLine={false} />
          <Tooltip
            cursor={{ stroke: "var(--axis)" }}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <TipBox title={fmtDate(String(label))} rows={[{ label: "ICE “C”", value: `$${payload[0].payload.c.toFixed(4)}`, color: "var(--series-1)" }]} />
              ) : null
            }
          />
          <Line type="monotone" dataKey="c" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
