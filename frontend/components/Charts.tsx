"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipProps } from "recharts";
import type { BenchHistory, Curve, HistoryPoint, Signals, TradePoint } from "@/lib/api";
import { fmtIn, useMoney } from "@/lib/prefs";

const AXIS = {
  stroke: "var(--axis)",
  tick: { fill: "var(--text-muted)", fontSize: 11, fontFamily: "var(--font-mono)" },
  tickLine: false,
};
const fmtDate = (d: string) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
const fmtDateY = (d: string) =>
  new Date(d + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
const fmtMonth = (d: string) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { month: "short", year: "2-digit" });
const fmtYear = (d: string) => new Date(d + "T00:00:00").getFullYear().toString();

type Row = { label: string; value: string; sub?: string; color?: string };

function TipBox({ title, rows }: { title: string; rows: Row[] }) {
  return (
    <div className="min-w-[180px] rounded-lg border border-border bg-surface px-3 py-2 text-sm shadow-pop">
      <div className="mb-1 text-[11px] text-muted">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-baseline justify-between gap-4 py-0.5">
          <span className="inline-flex items-center gap-1.5 text-ink2">
            {r.color && <span className="inline-block h-0.5 w-3 rounded" style={{ background: r.color }} />}
            {r.label}
          </span>
          <span className="mono text-right">
            <span className="font-semibold">{r.value}</span>
            {r.sub && <span className="ml-1 text-[11px] text-muted">{r.sub}</span>}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Tooltip row for a USD/lb value: primary unit bold, other unit muted. */
function useDualRow() {
  const m = useMoney();
  return (label: string, usdLb: number | null | undefined, color?: string, digits = 2, extra = ""): Row => ({
    label,
    color,
    value: usdLb == null ? "—" : `${fmtIn(usdLb, m.unit, digits)}/${m.unit}${extra}`,
    sub: usdLb == null ? undefined : `${fmtIn(usdLb, m.alt, digits)}/${m.alt}`,
  });
}

export function Legend({ items }: { items: { label: string; color: string; dot?: boolean; dashed?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink2">
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5">
          {i.dot ? (
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: i.color }} />
          ) : (
            <span
              className="inline-block h-0 w-4"
              style={{ borderTop: `2px ${i.dashed ? "dashed" : "solid"} ${i.color}` }}
            />
          )}
          {i.label}
        </span>
      ))}
    </div>
  );
}

const yMoney = (v: number) => `$${v.toFixed(2)}`;

/** Spot average vs ICE "C" (and ICE Robusta when loaded) — same unit, one axis. */
export function PriceChart({ points, label, showRobusta = false }: { points: HistoryPoint[]; label: string; showRobusta?: boolean }) {
  const m = useMoney();
  const row = useDualRow();
  const data = points.map((p) => ({
    date: p.date,
    spot: m.conv(p.spot_avg_usd_lb),
    c: m.conv(p.ice_c_usd_lb),
    r: m.conv(p.ice_robusta_usd_lb ?? null),
    raw: p,
  }));
  const sparse = data.filter((d) => d.spot !== null).length < 10;
  const long = points.length > 1 && new Date(points[points.length - 1].date).getTime() - new Date(points[0].date).getTime() > 400 * 864e5;
  const hasRob = showRobusta && data.some((d) => d.r !== null);
  return (
    <div className="h-72 w-full">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="date" tickFormatter={long ? fmtMonth : fmtDate} minTickGap={40} {...AXIS} />
          <YAxis tickFormatter={yMoney} width={56} domain={["auto", "auto"]} {...AXIS} axisLine={false} />
          <Tooltip
            cursor={{ stroke: "var(--axis)" }}
            content={({ active, payload, label: l }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0].payload.raw as HistoryPoint;
              const rows = [
                row(`${label} spot avg`, p.spot_avg_usd_lb, "var(--series-2)", 2, p.spot_n ? ` · ${p.spot_n}` : ""),
                row("ICE “C”", p.ice_c_usd_lb, "var(--series-1)", 4),
              ];
              if (hasRob) rows.push(row("ICE Robusta", p.ice_robusta_usd_lb ?? null, "var(--series-4)", 4));
              return <TipBox title={fmtDateY(String(l))} rows={rows} />;
            }}
          />
          <Line type="monotone" dataKey="c" stroke="var(--series-1)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
          {hasRob && (
            <Line type="monotone" dataKey="r" stroke="var(--series-4)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
          )}
          <Line
            type="monotone"
            dataKey="spot"
            stroke="var(--series-2)"
            strokeWidth={2}
            dot={sparse ? { r: 4, strokeWidth: 2, stroke: "var(--surface-1)", fill: "var(--series-2)" } : false}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Origin differential over the benchmark — a single series around a zero baseline. */
export function DiffChart({ points }: { points: HistoryPoint[] }) {
  const m = useMoney();
  const row = useDualRow();
  const data = points.filter((p) => p.diff_usd_lb !== null).map((p) => ({ date: p.date, diff: m.conv(p.diff_usd_lb), raw: p.diff_usd_lb }));
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
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const v = payload[0].payload.raw as number;
              const r = row("Differential", v, "var(--series-2)");
              r.value = `${fmtIn(v, m.unit, 2, true)}/${m.unit}`;
              r.sub = `${fmtIn(v, m.alt, 2, true)}/${m.alt}`;
              return <TipBox title={fmtDateY(String(label))} rows={[r]} />;
            }}
          />
          <Line type="monotone" dataKey="diff" stroke="var(--series-2)" strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

const REPORTER_COLOR: Record<string, string> = { USA: "var(--series-1)", DEU: "var(--series-2)", JPN: "var(--series-3)" };

/** Customs import unit values per reporter market: monthly points, plus annual averages (dashed, mid-year). */
export function TradeChart({ trade }: { trade: TradePoint[] }) {
  const m = useMoney();
  const row = useDualRow();
  const reporters = Array.from(new Set(trade.map((t) => t.reporter))).slice(0, 3);
  // Annual rows are plotted at mid-year so they sit in the middle of the months they average.
  const keyed = trade.map((t) => ({ ...t, x: t.freq === "A" ? `${t.period.slice(0, 4)}-07-01` : t.period }));
  const periods = Array.from(new Set(keyed.map((t) => t.x))).sort();
  const hasAnnual = keyed.some((t) => t.freq === "A");
  const data = periods.map((p) => {
    const r: Record<string, number | string | null> = { period: p };
    reporters.forEach((rep) => {
      const mo = keyed.find((t) => t.x === p && t.reporter === rep && t.freq === "M");
      const yr = keyed.find((t) => t.x === p && t.reporter === rep && t.freq === "A");
      r[rep] = mo ? m.conv(mo.unit_usd_lb) : null;
      r[`${rep}_raw`] = mo ? mo.unit_usd_lb : null;
      r[`${rep}_A`] = yr ? m.conv(yr.unit_usd_lb) : null;
      r[`${rep}_A_raw`] = yr ? yr.unit_usd_lb : null;
    });
    return r;
  });
  if (!data.length) return null;
  return (
    <>
      <Legend
        items={[
          ...reporters.map((r) => ({ label: `Imports into ${r}`, color: REPORTER_COLOR[r] ?? "var(--series-4)", dot: true })),
          ...(hasAnnual ? [{ label: "Annual average", color: "var(--text-muted)", dashed: true }] : []),
        ]}
      />
      <div className="mt-2 h-56 w-full">
        <ResponsiveContainer>
          <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="period" tickFormatter={fmtMonth} minTickGap={30} {...AXIS} />
            <YAxis tickFormatter={yMoney} width={56} domain={["auto", "auto"]} {...AXIS} axisLine={false} />
            <Tooltip
              cursor={{ stroke: "var(--axis)" }}
              content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <TipBox
                    title={fmtMonth(String(label))}
                    rows={reporters.flatMap((r) => {
                      const pl = payload[0].payload;
                      const out: Row[] = [];
                      if (pl[`${r}_raw`] != null) out.push(row(`${r} monthly`, pl[`${r}_raw`] as number, REPORTER_COLOR[r]));
                      if (pl[`${r}_A_raw`] != null) out.push(row(`${r} annual`, pl[`${r}_A_raw`] as number, REPORTER_COLOR[r]));
                      return out;
                    })}
                  />
                ) : null
              }
            />
            {hasAnnual &&
              reporters.map((r) => (
                <Line
                  key={`${r}_A`}
                  dataKey={`${r}_A`}
                  stroke={REPORTER_COLOR[r] ?? "var(--series-4)"}
                  strokeWidth={2}
                  strokeDasharray="5 4"
                  strokeOpacity={0.6}
                  dot={{ r: 3, strokeWidth: 0, fill: REPORTER_COLOR[r] ?? "var(--series-4)", fillOpacity: 0.6 }}
                  connectNulls
                  isAnimationActive={false}
                />
              ))}
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

/** ICE Arabica "C" and ICE Robusta over a selectable range (1M … MAX). */
export function BenchmarkChart({ hist, show }: { hist: BenchHistory; show: { arabica: boolean; robusta: boolean } }) {
  const m = useMoney();
  const row = useDualRow();
  const data = hist.points.map((p) => ({ date: p.date, a: m.conv(p.arabica), r: m.conv(p.robusta), raw: p }));
  const span = ["1m", "6m"].includes(hist.range) ? fmtDate : ["1y", "3y"].includes(hist.range) ? fmtMonth : fmtYear;
  const a = show.arabica && data.some((d) => d.a !== null);
  const r = show.robusta && data.some((d) => d.r !== null);
  const single = a !== r;
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="g-arabica" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--series-1)" stopOpacity={0.18} />
              <stop offset="100%" stopColor="var(--series-1)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="date" tickFormatter={span} minTickGap={48} {...AXIS} />
          <YAxis tickFormatter={yMoney} width={52} domain={["auto", "auto"]} {...AXIS} axisLine={false} />
          <Tooltip
            cursor={{ stroke: "var(--axis)" }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0].payload.raw as BenchHistory["points"][number];
              const rows: Row[] = [];
              if (a) rows.push(row("ICE Arabica “C”", p.arabica, "var(--series-1)", 4));
              if (r) rows.push(row("ICE Robusta", p.robusta, "var(--series-4)", 4));
              return <TipBox title={`${fmtDateY(String(label))}${hist.interval === "weekly" ? " · week" : ""}`} rows={rows} />;
            }}
          />
          {a && single && <Area type="monotone" dataKey="a" stroke="none" fill="url(#g-arabica)" isAnimationActive={false} />}
          {a && <Line type="monotone" dataKey="a" stroke="var(--series-1)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />}
          {r && <Line type="monotone" dataKey="r" stroke="var(--series-4)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Futures term structure: settle per delivery month, with the curve from ~a month ago for context. */
export function CurveChart({ curve }: { curve: Curve }) {
  const m = useMoney();
  const row = useDualRow();
  const prior = new Map(curve.month_ago.map((c) => [c.symbol, c.usd_lb]));
  const data = curve.contracts.map((c) => ({
    label: c.label,
    now: m.conv(c.usd_lb),
    then: m.conv(prior.get(c.symbol) ?? null),
    raw: c.usd_lb,
    rawThen: prior.get(c.symbol) ?? null,
  }));
  const hasPrior = data.some((d) => d.then !== null);
  return (
    <>
      <Legend
        items={[
          { label: `Settle ${curve.date ? fmtDate(curve.date) : ""}`, color: "var(--series-1)" },
          ...(hasPrior ? [{ label: `~1 month earlier`, color: "var(--axis)", dashed: true }] : []),
        ]}
      />
      <div className="mt-2 h-40 w-full">
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 24, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="label" {...AXIS} interval="preserveStartEnd" minTickGap={8} padding={{ left: 8, right: 8 }} />
            <YAxis tickFormatter={yMoney} width={48} domain={["auto", "auto"]} {...AXIS} axisLine={false} />
            <Tooltip
              cursor={{ stroke: "var(--axis)" }}
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload;
                const rows = [row("Settle", p.raw, "var(--series-1)", 4)];
                if (p.rawThen != null) rows.push(row("Month ago", p.rawThen, "var(--axis)", 4));
                return <TipBox title={`${label} contract`} rows={rows} />;
              }}
            />
            {hasPrior && (
              <Line type="monotone" dataKey="then" stroke="var(--axis)" strokeWidth={2} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
            )}
            <Line
              type="monotone"
              dataKey="now"
              stroke="var(--series-1)"
              strokeWidth={2}
              dot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-1)", fill: "var(--series-1)" }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

/** Certified stocks (bags) above ICE "C" — two aligned panels, never a dual axis. */
export function StocksChart({ series }: { series: Signals["certified_stocks"]["series"] }) {
  const m = useMoney();
  const row = useDualRow();
  const data = series.map((s) => ({ date: s.date, bags: s.bags, c: m.conv(s.ice_usd_lb), raw: s }));
  const tip = ({ active, payload, label }: TooltipProps<number, string>) => {
    if (!active || !payload?.length) return null;
    const s = (payload[0].payload as (typeof data)[number]).raw;
    return (
      <TipBox
        title={fmtDateY(String(label))}
        rows={[{ label: "Certified bags", value: s.bags.toLocaleString(), color: "var(--series-3)" }, row("ICE “C”", s.ice_usd_lb, "var(--series-1)", 4)]}
      />
    );
  };
  return (
    <div>
      <div className="h-28 w-full">
        <ResponsiveContainer>
          <ComposedChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: 0 }} syncId="stocks">
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="date" hide />
            <YAxis tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} width={48} {...AXIS} axisLine={false} domain={["auto", "auto"]} />
            <Tooltip cursor={{ stroke: "var(--axis)" }} content={tip} />
            <Area type="monotone" dataKey="bags" stroke="var(--series-3)" strokeWidth={2} fill="var(--series-3)" fillOpacity={0.12} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="h-28 w-full">
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: 0 }} syncId="stocks">
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="date" tickFormatter={fmtMonth} minTickGap={40} {...AXIS} />
            <YAxis tickFormatter={yMoney} width={48} {...AXIS} axisLine={false} domain={["auto", "auto"]} />
            <Tooltip cursor={{ stroke: "var(--axis)" }} content={tip} />
            <Line type="monotone" dataKey="c" stroke="var(--series-1)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
