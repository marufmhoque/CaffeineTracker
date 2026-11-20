"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { DiffChart, Legend, PriceChart, TradeChart } from "@/components/Charts";
import { Empty, ErrorNote, PageHeader, Segmented } from "@/components/ui";
import { useApi, type History, type Matrix } from "@/lib/api";
import { useMoney } from "@/lib/prefs";

type Range = "30d" | "90d" | "1y" | "3y";

function Trends() {
  const m = useMoney();
  const router = useRouter();
  const params = useSearchParams();
  const country = params.get("country") || "COL";
  const process = params.get("process") || "";
  const [range, setRange] = useState<Range>((params.get("range") as Range) || "1y");
  const [showTable, setShowTable] = useState(false);

  const matrix = useApi<Matrix>("/matrix");
  const qs = new URLSearchParams({ country, range });
  if (process) qs.set("process", process);
  const { data, error, loading } = useApi<History>(`/history?${qs}`);

  const setParam = (k: string, v: string) => {
    const p = new URLSearchParams(params.toString());
    if (v) p.set(k, v);
    else p.delete(k);
    router.replace(`/trends?${p}`);
  };

  const c = matrix.data?.countries.find((x) => x.iso_code === country);
  const name = c?.name ?? country;
  const hasSpot = data?.points.some((p) => p.spot_avg_usd_lb !== null);
  const trend = data?.spread_trend;

  return (
    <>
      <PageHeader title="Historical trends" subtitle="Spot prices vs the ICE “C” benchmark, the origin differential spread, and customs import unit values.">
        <select className="input" value={country} onChange={(e) => setParam("country", e.target.value)} aria-label="Country">
          {(matrix.data?.countries ?? []).map((x) => (
            <option key={x.iso_code} value={x.iso_code}>
              {x.name}
            </option>
          ))}
        </select>
        <select className="input" value={process} onChange={(e) => setParam("process", e.target.value)} aria-label="Process">
          <option value="">All processes</option>
          {data?.processes.map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
        <Segmented
          label="Range"
          value={range}
          onChange={(r) => {
            setRange(r);
            setParam("range", r);
          }}
          options={[
            { value: "30d", label: "30d" },
            { value: "90d", label: "90d" },
            { value: "1y", label: "1y" },
            { value: "3y", label: "3y" },
          ]}
        />
      </PageHeader>
      <ErrorNote error={error} />

      <div className={`grid gap-4 transition-opacity lg:grid-cols-3 ${loading ? "opacity-60" : ""}`}>
        <section className="card lg:col-span-2">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-medium">
              {name}
              {process ? ` · ${process}` : ""} — spot vs ICE “C” {m.suffix}
            </h2>
            <Legend
              items={[
                { label: `${name} spot avg`, color: "var(--series-1)" },
                { label: "ICE “C”", color: "var(--series-2)" },
              ]}
            />
          </div>
          {data && data.points.length ? <PriceChart points={data.points} label={name} /> : <div className="h-72" />}
          {data && !hasSpot && (
            <p className="mt-2 text-sm text-ink2">No importer spot offers recorded for this origin in the range yet — see customs unit values below.</p>
          )}
        </section>

        <section className="card">
          <h2 className="mb-1 font-medium">Differential spread</h2>
          <p className="mb-2 text-xs text-muted">Origin spot premium over ICE “C”. Widening = origin getting dearer relative to the market.</p>
          {trend ? (
            <div className="mb-2 text-sm">
              <span className="text-2xl font-semibold">{trend.direction}</span>
              <span className="ml-2 text-ink2">
                {m.fmtSigned(trend.usd_lb_per_30d, 3)}
                {m.suffix} per 30 days
              </span>
            </div>
          ) : (
            <div className="mb-2 text-sm text-muted">Needs ≥3 spot observations in range.</div>
          )}
          {c && (
            <div className="space-y-1 border-t border-[var(--grid)] pt-2 text-sm">
              <div className="flex justify-between">
                <span className="text-ink2">Current differential</span>
                <span className="num font-medium">{m.fmtSigned(c.diff_vs_c_usd_lb)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink2">Typical range</span>
                <span className="num">
                  {m.fmtSigned(c.typical_diff_usd_lb[0])} … {m.fmtSigned(c.typical_diff_usd_lb[1])}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink2">Harvest</span>
                <span>{c.harvest_status}</span>
              </div>
            </div>
          )}
          {data && data.points.some((p) => p.diff_usd_lb !== null) && <DiffChart points={data.points} />}
        </section>
      </div>

      <section className="card mt-4">
        <h2 className="mb-1 font-medium">Customs import unit values {m.suffix}</h2>
        <p className="mb-2 text-xs text-muted">HS 0901.11 (green, not decaf): customs value ÷ net weight for imports from {name}. Broad market signal across all grades.</p>
        {data?.trade.some((t) => t.freq === "M") ? (
          <TradeChart trade={data.trade} />
        ) : data?.trade.length ? (
          <ul className="text-sm">
            {data.trade.map((t) => (
              <li key={`${t.reporter}${t.period}`}>
                {t.period.slice(0, 4)} annual · {t.reporter}: <span className="num font-medium">{m.fmt(t.unit_usd_lb)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="No customs data for this origin in range" />
        )}
      </section>

      <section className="mt-4">
        <button className="btn" onClick={() => setShowTable((s) => !s)} aria-expanded={showTable}>
          {showTable ? "Hide" : "Show"} data table
        </button>
        {showTable && data && (
          <div className="card mt-2 max-h-96 overflow-auto p-0">
            <table className="w-full">
              <thead className="sticky top-0 bg-surface">
                <tr>
                  <th className="th">Date</th>
                  <th className="th num">Spot avg</th>
                  <th className="th num">Offers</th>
                  <th className="th num">ICE “C”</th>
                  <th className="th num">Differential</th>
                </tr>
              </thead>
              <tbody>
                {[...data.points].reverse().map((p) => (
                  <tr key={p.date} className="border-t border-[var(--grid)]">
                    <td className="td">{p.date}</td>
                    <td className="td num">{m.fmt(p.spot_avg_usd_lb)}</td>
                    <td className="td num">{p.spot_n || "—"}</td>
                    <td className="td num">{m.fmt(p.ice_c_usd_lb, 4)}</td>
                    <td className="td num">{m.fmtSigned(p.diff_usd_lb)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

export default function TrendsPage() {
  return (
    <Suspense>
      <Trends />
    </Suspense>
  );
}
