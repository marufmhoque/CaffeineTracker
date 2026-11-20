"use client";

import { geoEqualEarth, geoPath } from "d3-geo";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import { useMemo, useRef, useState } from "react";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import world from "world-atlas/countries-50m.json";
import type { Country } from "@/lib/api";
import { fmtPct, useMoney } from "@/lib/prefs";

export type MapMetric = "price" | "diff" | "chg30";

const SEQ = ["--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5", "--seq-6", "--seq-7"].map((v) => `var(${v})`);
// Diverging: blue (prices falling = good for a buyer) <-> neutral midpoint <-> rose (rising).
const DIV = ["#1d4ed8", "#3b82f6", "#93c5fd", "var(--neutral-mid)", "#fda4af", "#f43f5e", "#be123c"];
const DIV_EDGES = [-10, -5, -2, 2, 5, 10];

// world-atlas uses ISO 3166 numeric ids; Comtrade reports Taiwan under 490.
const MAP_ID_OVERRIDE: Record<string, number> = { TWN: 158 };

type Geo = Feature<Geometry, { name: string }> & { id?: string | number };

const W = 960;
const H = 470;

function metricValue(c: Country, m: MapMetric): number | null {
  if (m === "price") return c.spot_avg_usd_lb ?? c.trade_unit_usd_lb;
  if (m === "diff") return c.diff_vs_c_usd_lb;
  return c.chg_30d_pct;
}

function quantileEdges(values: number[], bins: number): number[] {
  const s = [...values].sort((a, b) => a - b);
  const edges: number[] = [];
  for (let i = 1; i < bins; i++) edges.push(s[Math.min(s.length - 1, Math.floor((i / bins) * s.length))]);
  return edges;
}

function binIndex(v: number, edges: number[]) {
  let i = 0;
  while (i < edges.length && v > edges[i]) i++;
  return i;
}

export default function WorldMap({
  rows,
  metric,
  onSelect,
  selected,
}: {
  rows: Country[];
  metric: MapMetric;
  onSelect?: (iso: string) => void;
  selected?: string | null;
}) {
  const money = useMoney();
  const wrap = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ c: Country | null; name: string; x: number; y: number } | null>(null);

  const features = useMemo(() => {
    const topo = world as unknown as Topology<{ countries: GeometryCollection<{ name: string }> }>;
    const fc = feature(topo, topo.objects.countries) as unknown as FeatureCollection<Geometry, { name: string }>;
    return fc.features.filter((f) => String((f as Geo).id) !== "010") as Geo[]; // drop Antarctica
  }, []);

  const path = useMemo(() => {
    const proj = geoEqualEarth().fitExtent(
      [
        [4, 4],
        [W - 4, H - 4],
      ],
      { type: "FeatureCollection", features } as FeatureCollection,
    );
    return geoPath(proj);
  }, [features]);

  const byId = useMemo(() => {
    const m = new Map<number, Country>();
    rows.forEach((c) => m.set(MAP_ID_OVERRIDE[c.iso_code] ?? c.m49, c));
    return m;
  }, [rows]);

  const { colorOf, legend } = useMemo(() => {
    const vals = rows.map((c) => metricValue(c, metric)).filter((v): v is number => v !== null);
    if (metric === "chg30") {
      return {
        colorOf: (v: number) => DIV[binIndex(v, DIV_EDGES)],
        legend: DIV.map((col, i) => ({
          col,
          label: i === 0 ? `< ${DIV_EDGES[0]}%` : i === DIV.length - 1 ? `> ${DIV_EDGES[i - 1]}%` : `${DIV_EDGES[i - 1]}…${DIV_EDGES[i]}%`,
        })),
      };
    }
    const edges = vals.length >= 7 ? quantileEdges(vals, 7) : [];
    return {
      colorOf: (v: number) => (edges.length ? SEQ[binIndex(v, edges)] : SEQ[3]),
      legend: edges.length
        ? SEQ.map((col, i) => ({
            col,
            label:
              i === 0
                ? `≤ ${money.fmt(edges[0])}`
                : i === SEQ.length - 1
                  ? `> ${money.fmt(edges[i - 1])}`
                  : `≤ ${money.fmt(edges[i])}`,
          }))
        : [],
    };
  }, [rows, metric, money]);

  const label = metric === "price" ? `Price ${money.suffix}` : metric === "diff" ? `Differential vs ICE “C” ${money.suffix}` : "30-day change";

  return (
    <div ref={wrap} className="relative" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`World map: ${label} by producing country`}>
        {features.map((f, i) => {
          const c = byId.get(Number(f.id));
          const v = c ? metricValue(c, metric) : null;
          const fill = !c ? "var(--land)" : v === null ? "var(--nodata)" : colorOf(v);
          const isSel = c && selected === c.iso_code;
          const d = path(f) ?? "";
          return (
            <path
              key={`${f.id}-${i}`}
              d={d}
              fill={fill}
              stroke={isSel ? "var(--text-primary)" : "var(--surface-1)"}
              strokeWidth={isSel ? 1.6 : 0.6}
              className={c ? "cursor-pointer outline-none transition-opacity hover:opacity-80 focus-visible:opacity-70" : ""}
              tabIndex={c ? 0 : undefined}
              role={c ? "button" : undefined}
              aria-label={c ? `${c.name}: open details` : undefined}
              onKeyDown={(e) => {
                if (c && (e.key === "Enter" || e.key === " ")) {
                  e.preventDefault();
                  onSelect?.(c.iso_code);
                }
              }}
              onMouseMove={(e) => {
                if (!c || !wrap.current) return setHover(null);
                const r = wrap.current.getBoundingClientRect();
                setHover({ c, name: c.name, x: e.clientX - r.left, y: e.clientY - r.top });
              }}
              onClick={() => c && onSelect?.(c.iso_code)}
            />
          );
        })}
      </svg>
      {hover?.c && <MapTip c={hover.c} metric={metric} x={hover.x} y={hover.y} width={wrap.current?.clientWidth ?? 600} />}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink2">
        <span className="font-medium text-ink">{label}</span>
        {legend.map((l) => (
          <span key={l.label} className="inline-flex items-center gap-1">
            <span className="inline-block h-3 w-4 rounded-sm" style={{ background: l.col }} />
            {l.label}
          </span>
        ))}
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-3 w-4 rounded-sm" style={{ background: "var(--nodata)" }} />
          No data
        </span>
      </div>
    </div>
  );
}

/** Elevated micro-card: country name and its headline price in both units (plus the active metric). */
function MapTip({ c, metric, x, y, width }: { c: Country; metric: MapMetric; x: number; y: number; width: number }) {
  const money = useMoney();
  const price = c.spot_avg_usd_lb ?? c.trade_unit_usd_lb;
  const d = money.dual(price);
  const flip = x > width - 240;
  return (
    <div
      className="pointer-events-none absolute z-10 rounded-lg border border-border bg-surface px-3 py-2 shadow-pop"
      style={{ left: flip ? undefined : x + 14, right: flip ? width - x + 14 : undefined, top: y + 14 }}
    >
      <div className="flex items-baseline gap-1.5 whitespace-nowrap text-sm">
        <span className="font-semibold">{c.name}</span>
        <span className="text-muted">·</span>
        <span className="mono font-semibold">{d.primary}</span>
        {price != null && <span className="mono text-xs text-muted">({d.secondary})</span>}
      </div>
      {metric === "diff" && c.diff_vs_c_usd_lb != null && (
        <div className="mono mt-0.5 text-xs text-ink2">{money.dual(c.diff_vs_c_usd_lb, 2, true).primary} vs “C”</div>
      )}
      {metric === "chg30" && c.chg_30d_pct != null && <div className="mono mt-0.5 text-xs text-ink2">{fmtPct(c.chg_30d_pct)} in 30 days</div>}
    </div>
  );
}
