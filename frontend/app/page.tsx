"use client";

import Link from "next/link";
import { useState } from "react";
import { BenchmarkChart, CurveChart, Legend, StocksChart } from "@/components/Charts";
import OriginDrawer from "@/components/OriginDrawer";
import {
  Badge,
  Delta,
  Empty,
  ErrorNote,
  PageHeader,
  Price,
  RiskBadge,
  Segmented,
  SpeciesToggle,
  StatTile,
  timeAgo,
  type Species,
} from "@/components/ui";
import WorldMap, { type MapMetric } from "@/components/WorldMap";
import { useApi, type Alert, type BenchHistory, type BenchRange, type Matrix, type Signals, type Summary } from "@/lib/api";
import { fmtPct, useMoney } from "@/lib/prefs";

const RANGES: { value: BenchRange; label: string }[] = [
  { value: "1m", label: "1M" },
  { value: "6m", label: "6M" },
  { value: "1y", label: "1Y" },
  { value: "3y", label: "3Y" },
  { value: "5y", label: "5Y" },
  { value: "max", label: "MAX" },
];

export default function Overview() {
  const m = useMoney();
  const [species, setSpecies] = useState<Species>("Arabica");
  const [range, setRange] = useState<BenchRange>("1y");
  const [metric, setMetric] = useState<MapMetric>("price");
  const [selected, setSelected] = useState<string | null>(null);

  const summary = useApi<Summary>("/benchmarks/summary");
  const hist = useApi<BenchHistory>(`/benchmarks/range?range=${range}`);
  const matrix = useApi<Matrix>(`/matrix?species=${species}`);
  const alerts = useApi<{ unread: number; alerts: Alert[] }>("/alerts?limit=6");
  const signals = useApi<Signals>("/signals");

  const ice = summary.data?.ice_arabica;
  const rob = summary.data?.ice_robusta;
  const rows = matrix.data?.countries ?? [];
  const priced = rows.filter((c) => c.spot_avg_usd_lb !== null || c.trade_unit_usd_lb !== null).length;
  const movers = [...rows].filter((c) => c.chg_30d_pct !== null).sort((a, b) => (a.chg_30d_pct ?? 0) - (b.chg_30d_pct ?? 0));
  const show = { arabica: species !== "Robusta", robusta: species !== "Arabica" };
  const rangeStats = species === "Robusta" ? hist.data?.robusta : hist.data?.arabica;

  return (
    <>
      <PageHeader
        title="Market overview"
        subtitle={`Benchmarks, origin prices and forward-looking signals for an Atlanta roaster. ${priced} of ${rows.length || "~70"} ${
          species === "All" ? "" : species + " "
        }origins currently priced.`}
      >
        <SpeciesToggle value={species} onChange={setSpecies} />
      </PageHeader>
      <ErrorNote error={summary.error || matrix.error} />

      {/* Benchmark bar */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {species === "Arabica" && (
          <>
            <StatTile label="ICE Arabica “C”" usdLb={ice?.usd_lb} digits={4} delta={ice?.chg_1d_pct} deltaLabel={ice?.date ? `1d · settle ${ice.date}` : "1d"} />
            <StatTile label="30-day moving avg" usdLb={ice?.ma30_usd_lb} digits={4} delta={ice?.chg_30d_pct} deltaLabel="30d" />
            <RangeTile label="52-week range" low={ice?.low_52w} high={ice?.high_52w} delta={ice?.chg_1y_pct} />
            <CurveTile curve={signals.data?.curve} />
          </>
        )}
        {species === "Robusta" && (
          <>
            {rob ? (
              <>
                <StatTile label="ICE Robusta (London)" usdLb={rob.usd_lb} digits={4} delta={rob.chg_1d_pct} deltaLabel={`1d · ${rob.date}`} />
                <StatTile label="Robusta 30-day change" value={fmtPct(rob.chg_30d_pct)} sub={<span className="text-muted">vs settle 30 days ago</span>} />
                <RangeTile label="Robusta 52-week range" low={rob.low_52w} high={rob.high_52w} delta={rob.chg_1y_pct} />
              </>
            ) : (
              <div className="card sm:col-span-2 lg:col-span-3">
                <div className="eyebrow">ICE Robusta (London)</div>
                <p className="mt-2 text-sm text-ink2">
                  ICE Robusta has no free data feed. Upload daily settlements (<code className="mono text-xs">date,usd_tonne</code>) on{" "}
                  <Link href="/data" className="link">
                    Data Sources
                  </Link>{" "}
                  to light up Robusta benchmarks, differentials and charts.
                </p>
              </div>
            )}
            <SurplusTile signals={signals.data} species="Robusta" />
          </>
        )}
        {species === "All" && (
          <>
            <StatTile label="ICE Arabica “C”" usdLb={ice?.usd_lb} digits={4} delta={ice?.chg_1d_pct} deltaLabel="1d" />
            <StatTile
              label="ICE Robusta"
              usdLb={rob?.usd_lb ?? null}
              digits={4}
              delta={rob?.chg_1d_pct}
              deltaLabel="1d"
              sub={!rob ? <Link href="/data" className="link">Add Robusta settlements</Link> : undefined}
            />
            <StatTile
              label="Arabica – Robusta spread"
              usdLb={ice?.usd_lb != null && rob?.usd_lb != null ? ice.usd_lb - rob.usd_lb : null}
              digits={4}
              sub={<span className="text-muted">arbitrage between the two contracts</span>}
            />
            <SurplusTile signals={signals.data} species="All" />
          </>
        )}
      </div>

      {/* Benchmark history */}
      <section className="card mt-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="card-title">
              {species === "Robusta" ? "ICE Robusta" : species === "All" ? "ICE Arabica “C” & Robusta" : "ICE Arabica “C”"}{" "}
              <span className="font-normal text-muted">
                {m.suffix} · {hist.data?.interval ?? "daily"}
              </span>
            </h2>
            {rangeStats && (
              <div className="mono mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink2">
                <Delta value={rangeStats.chg_pct} label={`over ${RANGES.find((r) => r.value === range)?.label}`} />
                <span>
                  High {m.dualText(rangeStats.high, 2)} · Low {m.dualText(rangeStats.low, 2)}
                </span>
              </div>
            )}
          </div>
          <Segmented label="Chart range" value={range} onChange={setRange} options={RANGES} size="sm" />
        </div>
        {species === "All" && (
          <Legend
            items={[
              { label: "ICE Arabica “C”", color: "var(--series-1)" },
              { label: "ICE Robusta", color: "var(--series-4)" },
            ]}
          />
        )}
        {hist.data?.points.length ? (
          <BenchmarkChart hist={hist.data} show={show} />
        ) : (
          <Empty title="No benchmark history yet">Run the benchmarks job from Data Sources.</Empty>
        )}
        {range === "max" && hist.data?.earliest && <p className="mt-1 text-xs text-muted">History since {hist.data.earliest}.</p>}
      </section>

      {/* Map + alerts */}
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <section className="card lg:col-span-2">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="card-title">Origins at a glance</h2>
            <Segmented
              label="Map metric"
              size="sm"
              value={metric}
              onChange={setMetric}
              options={[
                { value: "price", label: "Price" },
                { value: "diff", label: "Differential" },
                { value: "chg30", label: "30d change" },
              ]}
            />
          </div>
          {rows.length ? <WorldMap rows={rows} metric={metric} onSelect={setSelected} selected={selected} /> : <div className="h-80" />}
          <p className="mt-2 text-xs text-muted">
            Price is the average live spot offer, or the latest customs import unit value where nothing is offered. Click a country for its farms, lots and
            outlook.
          </p>
        </section>

        <section className="card">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="card-title">Latest alerts</h2>
            <Link href="/alerts" className="link text-xs">
              All alerts →
            </Link>
          </div>
          {alerts.data?.alerts.length ? (
            <ul className="divide-y divide-[var(--border)]">
              {alerts.data.alerts.map((a) => (
                <li key={a.id} className="py-2.5 text-sm">
                  <div className={a.is_read ? "text-ink2" : "font-medium"}>{a.message}</div>
                  <div className="mt-0.5 text-xs text-muted">{timeAgo(a.created_at)}</div>
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="No alerts yet">Alerts are generated daily at 7:45 ET once spot offers and benchmarks are loaded.</Empty>
          )}
        </section>
      </div>

      {/* Predictive signals */}
      <div className="mb-3 mt-8 flex items-end justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Predictive signals & market outlook</h2>
          <p className="text-sm text-ink2">Leading indicators for where green prices are heading — curve, weather, exchange stocks and crop cycle.</p>
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <CurveCard s={signals.data} />
        <WeatherCard s={signals.data} onSelect={setSelected} />
        <StocksCard s={signals.data} />
        <CropCard s={signals.data} onSelect={setSelected} />
      </div>

      {/* FX + movers */}
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <section className="card">
          <h2 className="card-title">Origin FX vs USD</h2>
          <p className="mb-2 mt-1 text-xs text-muted">Local currency per USD. A rising rate means the producer currency is weakening — often a softer export price ahead.</p>
          <table className="w-full">
            <thead>
              <tr>
                <th className="th !pl-0">CCY</th>
                <th className="th num">Per USD</th>
                <th className="th num">7d</th>
                <th className="th num !pr-0">30d</th>
              </tr>
            </thead>
            <tbody>
              {summary.data?.fx.map((f) => (
                <tr key={f.currency} className="border-t border-border">
                  <td className="td !pl-0 font-medium">{f.currency}</td>
                  <td className="td num">{f.per_usd.toLocaleString(undefined, { maximumFractionDigits: f.per_usd > 100 ? 0 : 3 })}</td>
                  <td className="td num">
                    <Delta value={f.chg_7d_pct} />
                  </td>
                  <td className="td num !pr-0 text-ink2">{fmtPct(f.chg_30d_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {movers.length > 0 && (
          <section className="card lg:col-span-2">
            <h2 className="card-title mb-2">Biggest 30-day movers</h2>
            <div className="grid gap-x-8 sm:grid-cols-2">
              {[movers.slice(0, 5), movers.slice(-5).reverse()].map((list, i) => (
                <ul key={i}>
                  <li className="eyebrow py-1">{i === 0 ? "Falling" : "Rising"}</li>
                  {list.map((c) => (
                    <li key={c.iso_code} className="flex items-center justify-between border-t border-border py-2 text-sm">
                      <button onClick={() => setSelected(c.iso_code)} className="text-left hover:underline">
                        {c.name}
                      </button>
                      <span className="flex items-center gap-3">
                        <Price usdLb={c.spot_avg_usd_lb ?? c.trade_unit_usd_lb} layout="stack" />
                        <Delta value={c.chg_30d_pct} />
                      </span>
                    </li>
                  ))}
                </ul>
              ))}
            </div>
          </section>
        )}
      </div>

      <OriginDrawer iso={selected} onClose={() => setSelected(null)} />
    </>
  );
}

function RangeTile({ label, low, high, delta }: { label: string; low?: number | null; high?: number | null; delta?: number | null }) {
  const m = useMoney();
  return (
    <div className="card card-hover">
      <div className="eyebrow">{label}</div>
      {low != null && high != null ? (
        <div className="mt-3">
          <div className="mono text-xl font-semibold leading-none tracking-tight">
            {m.fmt(low)}–{m.fmt(high)}
            <span className="text-sm font-normal text-muted">{m.suffix}</span>
          </div>
          <div className="mono mt-1 text-xs text-muted">
            {m.dual(low).secondary.replace(m.altSuffix, "")}–{m.dual(high).secondary}
          </div>
        </div>
      ) : (
        <div className="mono mt-3 text-xl text-muted">—</div>
      )}
      <div className="mt-3">
        <Delta value={delta} label="1y" />
      </div>
    </div>
  );
}

function CurveTile({ curve }: { curve?: Signals["curve"] }) {
  const st = curve?.structure;
  return (
    <div className="card card-hover">
      <div className="eyebrow">Term structure</div>
      <div className="mt-3 text-[22px] font-semibold leading-none tracking-tight">{st?.state ?? "—"}</div>
      <div className="mono mt-1 text-xs text-muted">
        {st ? `${st.slope_pct > 0 ? "+" : ""}${st.slope_pct.toFixed(1)}% front → ${st.months} mo out` : "Forward curve loading"}
      </div>
      <div className="mt-3">
        {st && <Badge tone={st.state === "Backwardation" ? "warn" : st.state === "Contango" ? "pos" : "neutral"}>{st.state === "Backwardation" ? "Tight nearby supply" : st.state === "Contango" ? "Ample nearby supply" : "No strong signal"}</Badge>}
      </div>
    </div>
  );
}

function SurplusTile({ signals, species }: { signals: Signals | null; species: Species }) {
  const w = signals?.crops.world;
  const yoy = species === "Robusta" ? w?.robusta_yoy_pct : w?.arabica_yoy_pct;
  return (
    <div className="card card-hover">
      <div className="eyebrow">World balance {w ? `· USDA ${w.market_year}` : ""}</div>
      <div className="mono mt-3 text-[22px] font-semibold leading-none tracking-tight">
        {w?.surplus_pct != null ? `${w.surplus_pct > 0 ? "+" : ""}${w.surplus_pct.toFixed(1)}%` : "—"}
      </div>
      <div className="mt-1 text-xs text-muted">{w ? (w.surplus_k_bags >= 0 ? "production surplus vs consumption" : "production deficit vs consumption") : "USDA data loading"}</div>
      <div className="mt-3">
        <Delta value={yoy ?? null} label={species === "Robusta" ? "Robusta crop y/y" : "Arabica crop y/y"} />
      </div>
    </div>
  );
}

function CurveCard({ s }: { s: Signals | null }) {
  const c = s?.curve;
  const st = c?.structure;
  return (
    <section className="card">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="card-title">ICE “C” forward curve</h3>
          <p className="text-xs text-muted">Settle by delivery month{c?.date ? ` · ${c.date}` : ""}</p>
        </div>
        {st && (
          <Badge tone={st.state === "Backwardation" ? "warn" : st.state === "Contango" ? "pos" : "neutral"}>
            {st.state} · <span className="mono">{st.slope_pct > 0 ? "+" : ""}{st.slope_pct.toFixed(1)}%</span>
          </Badge>
        )}
      </div>
      {c?.contracts.length ? (
        <>
          <div className="mt-3">
            <CurveChart curve={c} />
          </div>
          {st && <p className="mt-2 text-sm text-ink2">{st.takeaway}</p>}
        </>
      ) : (
        <div className="mt-3">
          <Empty title="Forward curve not loaded">Run the market job on Data Sources.</Empty>
        </div>
      )}
    </section>
  );
}

function WeatherCard({ s, onSelect }: { s: Signals | null; onSelect: (iso: string) => void }) {
  const w = s?.weather ?? [];
  const counts = { Critical: 0, Moderate: 0, Low: 0 } as Record<string, number>;
  w.forEach((b) => (counts[b.risk] = (counts[b.risk] ?? 0) + 1));
  return (
    <section className="card">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="card-title">Origin weather anomalies</h3>
          <p className="text-xs text-muted">90-day rain vs 10-year normal, heat and frost (incl. 10-day forecast) · Open-Meteo</p>
        </div>
        <div className="flex gap-1">
          {counts.Critical > 0 && <RiskBadge risk="Critical" prefix={`${counts.Critical}`} />}
          {counts.Moderate > 0 && <RiskBadge risk="Moderate" prefix={`${counts.Moderate}`} />}
        </div>
      </div>
      {w.length ? (
        <ul className="mt-3 max-h-[260px] divide-y divide-[var(--border)] overflow-y-auto pr-1">
          {w.map((b) => (
            <li key={b.belt}>
              <button onClick={() => onSelect(b.country_iso)} className="flex w-full items-center justify-between gap-3 py-2 text-left hover:bg-[var(--surface-hover)]">
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{b.belt}</span>
                  <span className="block truncate text-xs text-muted">
                    {b.country_iso} · {b.species} · {b.risk === "Low" ? "within normal range" : b.reasons.join(" · ")}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <span className="mono text-xs text-ink2" title="Rain, % of normal (90 days)">
                    {b.rain_pct_normal != null ? `${b.rain_pct_normal.toFixed(0)}%` : "—"}
                  </span>
                  <RiskBadge risk={b.risk} prefix="" />
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-3">
          <Empty title="Weather not loaded">Run the weather job on Data Sources.</Empty>
        </div>
      )}
      <p className="mt-2 text-[11px] text-muted">Risk is a rule-of-thumb score from real observations, not a crop forecast.</p>
    </section>
  );
}

function StocksCard({ s }: { s: Signals | null }) {
  const cs = s?.certified_stocks;
  return (
    <section className="card">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="card-title">ICE certified stocks vs “C”</h3>
          <p className="text-xs text-muted">Exchange-graded bags in licensed warehouses</p>
        </div>
        {cs?.latest && (cs.pinch ? <Badge tone="neg">Supply pinch</Badge> : <Badge tone="pos">No pinch</Badge>)}
      </div>
      {cs?.latest ? (
        <>
          <div className="mt-3 flex flex-wrap gap-6">
            <div>
              <div className="eyebrow">Latest · {cs.latest.date}</div>
              <div className="mono mt-1 text-xl font-semibold">{cs.latest.bags.toLocaleString()} bags</div>
            </div>
            <div>
              <div className="eyebrow">90 days</div>
              <div className="mt-1.5">
                <Delta value={cs.chg_90d_pct} />
              </div>
            </div>
            {cs.range_5y && (
              <div>
                <div className="eyebrow">Position in 5y range</div>
                <div className="mono mt-1 text-xl font-semibold">{cs.range_5y.position_pct.toFixed(0)}%</div>
              </div>
            )}
          </div>
          <div className="mt-3">
            <StocksChart series={cs.series} />
          </div>
          {cs.takeaway && <p className="mt-2 text-sm text-ink2">{cs.takeaway}</p>}
        </>
      ) : (
        <div className="mt-3">
          <Empty title="No certified stock data">
            ICE publishes these daily but has no free API. Upload a <code className="mono text-xs">date,bags</code> CSV on{" "}
            <Link href="/data" className="link">
              Data Sources
            </Link>
            .
          </Empty>
        </div>
      )}
    </section>
  );
}

function CropCard({ s, onSelect }: { s: Signals | null; onSelect: (iso: string) => void }) {
  const c = s?.crops;
  const w = c?.world;
  return (
    <section className="card">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="card-title">USDA FAS crop cycle</h3>
          <p className="text-xs text-muted">Production estimates, thousand 60 kg bags{w ? ` · ${w.market_year}` : ""}</p>
        </div>
        {w?.surplus_pct != null && (
          <Badge tone={w.surplus_pct >= 0 ? "pos" : "neg"}>
            World {w.surplus_pct >= 0 ? "surplus" : "deficit"} <span className="mono">{w.surplus_pct > 0 ? "+" : ""}{w.surplus_pct.toFixed(1)}%</span>
          </Badge>
        )}
      </div>
      {c?.origins.length ? (
        <table className="mt-3 w-full">
          <thead>
            <tr>
              <th className="th !pl-0">Origin</th>
              <th className="th">Cycle</th>
              <th className="th num">Crop</th>
              <th className="th num">y/y</th>
              <th className="th num !pr-0">vs 5y avg</th>
            </tr>
          </thead>
          <tbody>
            {c.origins.slice(0, 7).map((o) => (
              <tr key={o.iso_code} className="border-t border-border">
                <td className="td !pl-0">
                  <button onClick={() => onSelect(o.iso_code)} className="font-medium hover:underline">
                    {o.name}
                  </button>
                </td>
                <td className="td">
                  {o.cycle ? <Badge tone={o.cycle === "On-year" ? "pos" : "warn"}>{o.cycle}</Badge> : <span className="text-xs text-muted">—</span>}
                </td>
                <td className="td num">{(o.production_k_bags / 1000).toFixed(1)}M</td>
                <td className="td num">
                  <Delta value={o.yoy_pct} />
                </td>
                <td className="td num !pr-0">
                  <Delta value={o.vs_5y_avg_pct} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="mt-3">
          <Empty title="USDA data not loaded">Run the USDA job on Data Sources.</Empty>
        </div>
      )}
      <p className="mt-2 text-[11px] text-muted">On/off-year is derived from the alternating Arabica crop (Arabica-majority origins only). Surplus = world production − consumption.</p>
    </section>
  );
}
