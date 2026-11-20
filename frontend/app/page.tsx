"use client";

import Link from "next/link";
import { useState } from "react";
import { IceChart } from "@/components/Charts";
import { Delta, Empty, ErrorNote, PageHeader, Segmented, StatTile, timeAgo } from "@/components/ui";
import WorldMap, { type MapMetric } from "@/components/WorldMap";
import { useApi, type Alert, type Matrix, type Summary } from "@/lib/api";
import { fmtPct, useMoney } from "@/lib/prefs";

export default function Overview() {
  const m = useMoney();
  const summary = useApi<Summary>("/benchmarks/summary");
  const hist = useApi<{ date: string; ice_arabica_usd_lb: number | null }[]>("/benchmarks/history?days=365");
  const matrix = useApi<Matrix>("/matrix");
  const alerts = useApi<{ unread: number; alerts: Alert[] }>("/alerts?limit=8");
  const [metric, setMetric] = useState<MapMetric>("price");

  const ice = summary.data?.ice_arabica;
  const rows = matrix.data?.countries ?? [];
  const priced = rows.filter((c) => c.spot_avg_usd_lb !== null || c.trade_unit_usd_lb !== null).length;
  const movers = [...rows].filter((c) => c.chg_30d_pct !== null).sort((a, b) => (a.chg_30d_pct ?? 0) - (b.chg_30d_pct ?? 0));

  return (
    <>
      <PageHeader
        title="Market overview"
        subtitle={`Benchmarks, origin prices and buying signals for an Atlanta roaster. ${priced} of ${rows.length || "~70"} producing countries currently priced.`}
      />
      <ErrorNote error={summary.error || matrix.error} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="ICE Arabica “C”"
          value={ice?.usd_lb != null ? `${m.fmt(ice.usd_lb, 4)}` : "—"}
          delta={ice?.chg_1d_pct}
          deltaLabel="1d"
          sub={ice?.date ? <span className="text-muted">settle {ice.date}</span> : null}
        />
        <StatTile
          label="30-day moving avg"
          value={m.fmt(ice?.ma30_usd_lb ?? null, 4)}
          delta={ice?.chg_30d_pct}
          deltaLabel="30d"
        />
        <StatTile
          label="52-week range"
          value={ice?.low_52w != null ? `${m.fmt(ice.low_52w)}–${m.fmt(ice.high_52w)}` : "—"}
          delta={ice?.chg_1y_pct}
          deltaLabel="1y"
        />
        <StatTile
          label={summary.data?.ice_robusta ? "ICE Robusta" : summary.data?.ico ? "ICO composite" : "ICE Robusta"}
          value={
            summary.data?.ice_robusta
              ? m.fmt(summary.data.ice_robusta.usd_lb, 4)
              : summary.data?.ico
                ? m.fmt(summary.data.ico.composite_usd_lb, 4)
                : "—"
          }
          sub={
            !summary.data?.ice_robusta && !summary.data?.ico ? (
              <span className="text-muted">
                Add <code>data/ice_robusta.csv</code> or <code>ico_prices.csv</code>
              </span>
            ) : null
          }
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <section className="card lg:col-span-2">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-medium">Origins at a glance</h2>
            <Segmented
              label="Map metric"
              value={metric}
              onChange={setMetric}
              options={[
                { value: "price", label: "Price" },
                { value: "diff", label: "Differential" },
                { value: "chg30", label: "30d change" },
              ]}
            />
          </div>
          {rows.length ? <WorldMap rows={rows} metric={metric} /> : <div className="h-80" />}
          <p className="mt-2 text-xs text-muted">
            Price = average live spot offer where available, otherwise latest customs import unit value. Click a country for its trend.
          </p>
        </section>

        <section className="card">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="font-medium">Latest alerts</h2>
            <Link href="/alerts" className="text-sm text-accent hover:underline">
              All alerts →
            </Link>
          </div>
          {alerts.data?.alerts.length ? (
            <ul className="divide-y divide-[var(--grid)]">
              {alerts.data.alerts.map((a) => (
                <li key={a.id} className="py-2 text-sm">
                  <div className={a.is_read ? "text-ink2" : ""}>{a.message}</div>
                  <div className="mt-0.5 text-xs text-muted">{timeAgo(a.created_at)}</div>
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="No alerts yet">Alerts are generated daily at 7:45 ET once spot offers and benchmarks are loaded.</Empty>
          )}
        </section>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <section className="card lg:col-span-2">
          <h2 className="mb-2 font-medium">ICE Arabica “C”, 1 year {m.suffix}</h2>
          {hist.data?.length ? <IceChart data={hist.data} /> : <Empty title="No benchmark history yet">Run the benchmarks job from Data Sources.</Empty>}
        </section>
        <section className="card">
          <h2 className="mb-2 font-medium">Origin FX vs USD</h2>
          <p className="mb-2 text-xs text-muted">Local currency per USD. A rising rate means the producer currency is weakening — often a softer export price ahead.</p>
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">CCY</th>
                <th className="th num">Per USD</th>
                <th className="th num">7d</th>
                <th className="th num">30d</th>
              </tr>
            </thead>
            <tbody>
              {summary.data?.fx.map((f) => (
                <tr key={f.currency} className="border-t border-[var(--grid)]">
                  <td className="td font-medium">{f.currency}</td>
                  <td className="td num">{f.per_usd.toLocaleString(undefined, { maximumFractionDigits: f.per_usd > 100 ? 0 : 3 })}</td>
                  <td className="td num">
                    <Delta value={f.chg_7d_pct} invert />
                  </td>
                  <td className="td num">{fmtPct(f.chg_30d_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      {movers.length > 0 && (
        <section className="card mt-4">
          <h2 className="mb-2 font-medium">Biggest 30-day movers</h2>
          <div className="grid gap-x-8 sm:grid-cols-2">
            {[movers.slice(0, 5), movers.slice(-5).reverse()].map((list, i) => (
              <ul key={i}>
                <li className="py-1 text-xs uppercase tracking-wide text-muted">{i === 0 ? "Falling" : "Rising"}</li>
                {list.map((c) => (
                  <li key={c.iso_code} className="flex items-center justify-between border-t border-[var(--grid)] py-1.5 text-sm">
                    <Link href={`/trends?country=${c.iso_code}`} className="hover:underline">
                      {c.name}
                    </Link>
                    <span className="flex items-center gap-3">
                      <span className="num text-ink2">{m.fmt(c.spot_avg_usd_lb ?? c.trade_unit_usd_lb)}</span>
                      <Delta value={c.chg_30d_pct} />
                    </span>
                  </li>
                ))}
              </ul>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
