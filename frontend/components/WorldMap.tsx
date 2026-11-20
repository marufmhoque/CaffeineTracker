"use client";

import { geoEqualEarth, geoPath } from "d3-geo";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import world from "world-atlas/countries-50m.json";
import type { Country } from "@/lib/api";
import { fmtPct, useMoney } from "@/lib/prefs";

export type MapMetric = "price" | "diff" | "chg30";

const SEQ = ["--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5", "--seq-6", "--seq-7"].map((v) => `var(${v})`);
// Diverging: blue (prices falling = good for a buyer) <-> gray midpoint <-> red (rising).
const DIV = ["#1c5cab", "#5598e7", "#9ec5f4", "var(--neutral-mid)", "#f2a7a6", "#e66767", "#c23131"];
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

export default function WorldMap({ rows, metric }: { rows: Country[]; metric: MapMetric }) {
  const router = useRouter();
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
            label: i === 0 ? `≤ ${money.fmt(edges[0])}` : i === SEQ.length - 1 ? `> ${money.fmt(edges[i - 1])}` : `≤ ${money.fmt(edges[i])}`,
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
          const fill = !c ? "var(--surface-2)" : v === null ? "var(--nodata)" : colorOf(v);
          const d = path(f) ?? "";
          return (
            <path
              key={`${f.id}-${i}`}
              d={d}
              fill={fill}
              stroke="var(--surface-1)"
              strokeWidth={0.6}
              className={c ? "cursor-pointer transition-opacity hover:opacity-80" : ""}
              onMouseMove={(e) => {
                if (!c || !wrap.current) return setHover(null);
                const r = wrap.current.getBoundingClientRect();
                setHover({ c, name: c.name, x: e.clientX - r.left, y: e.clientY - r.top });
              }}
              onClick={() => c && router.push(`/trends?country=${c.iso_code}`)}
            />
          );
        })}
      </svg>
      {hover?.c && (
        <div
          className="pointer-events-none absolute z-10 w-60 rounded-lg border border-border bg-surface p-3 text-sm shadow-lg"
          style={{ left: Math.min(hover.x + 14, (wrap.current?.clientWidth ?? 600) - 250), top: hover.y + 14 }}
        >
          <div className="font-medium">{hover.c.name}</div>
          <div className="mb-1 text-xs text-muted">{hover.c.region} · {hover.c.harvest_status}</div>
          <Row k="Spot avg" v={hover.c.spot_avg_usd_lb !== null ? `${money.fmt(hover.c.spot_avg_usd_lb)} (${hover.c.spot_offer_count})` : "—"} />
          <Row k="Customs unit value" v={money.fmt(hover.c.trade_unit_usd_lb)} />
          <Row k="Diff vs “C”" v={money.fmtSigned(hover.c.diff_vs_c_usd_lb)} />
          <Row k="30-day change" v={fmtPct(hover.c.chg_30d_pct)} />
        </div>
      )}
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

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-ink2">{k}</span>
      <span className="num font-medium">{v}</span>
    </div>
  );
}
