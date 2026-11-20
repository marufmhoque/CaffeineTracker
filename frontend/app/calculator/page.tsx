"use client";

import { useEffect, useState } from "react";
import { Badge, ErrorNote, PageHeader, Price, Segmented } from "@/components/ui";
import { api, useApi, type FreightRules } from "@/lib/api";
import { useMoney } from "@/lib/prefs";

type Breakdown = {
  spot_usd_lb: number;
  freight_usd_lb: number;
  fee_usd_lb: number;
  landed_usd_lb: number;
  shrinkage: number;
  roasted_usd_lb: number;
  tariff_pct: number;
  tariff_usd_lb: number;
  ocean_usd_lb: number;
};

type Calc = {
  warehouse: { warehouse: string; tier_id: string; tier_label: string; freight_usd_lb: number; southeast: boolean; matched: boolean };
  per_lb: Breakdown;
  order?: {
    green_lb: number;
    roasted_lb: number;
    green_cost_usd: number;
    freight_usd: number;
    fees_usd: number;
    tariff_usd: number;
    ocean_usd: number;
    landed_total_usd: number;
  };
};

const usd = (v: number, d = 2) => `$${v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;

export default function Calculator() {
  const m = useMoney();
  const rulesApi = useApi<FreightRules>("/settings/freight");
  const [rules, setRules] = useState<FreightRules | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const [price, setPrice] = useState("4.25");
  const [unit, setUnit] = useState<"lb" | "kg" | "bag">("lb");
  const [bagKg, setBagKg] = useState("69");
  const [bags, setBags] = useState("10");
  const [warehouse, setWarehouse] = useState("Savannah, GA");
  const [freightOverride, setFreightOverride] = useState("");
  const [shrink, setShrink] = useState(15);
  const [tariff, setTariff] = useState("0");
  const [oceanBasis, setOceanBasis] = useState<"lb" | "container">("container");
  const [ocean, setOcean] = useState("0");
  const [containerBags, setContainerBags] = useState("320");
  const [calc, setCalc] = useState<Calc | null>(null);
  const [calcErr, setCalcErr] = useState<string | null>(null);
  const [compare, setCompare] = useState<{ name: string; landed: number; roasted: number; freight: number }[]>([]);

  useEffect(() => {
    if (rulesApi.data) {
      setRules(rulesApi.data);
      setShrink(Math.round(rulesApi.data.default_shrinkage * 1000) / 10);
      setTariff(String(Math.round((rulesApi.data.tariff_pct ?? 0) * 1000) / 10));
      if (rulesApi.data.ocean_surcharge_usd_lb) {
        setOceanBasis("lb");
        setOcean(String(rulesApi.data.ocean_surcharge_usd_lb));
      }
    }
  }, [rulesApi.data]);

  const body = (wh: string, override?: boolean) => ({
    price: Number(price),
    unit,
    bag_kg: Number(bagKg) || null,
    bags: Number(bags) || null,
    warehouse: wh,
    freight_usd_lb: override && freightOverride !== "" ? Number(freightOverride) : null,
    shrinkage: shrink / 100,
    tariff_pct: Number(tariff) || 0,
    ocean_usd_lb: oceanBasis === "lb" ? Number(ocean) || 0 : 0,
    ocean_usd_container: oceanBasis === "container" && Number(ocean) ? Number(ocean) : null,
    container_bags: Number(containerBags) || 320,
  });

  useEffect(() => {
    if (!(Number(price) > 0)) return;
    const t = setTimeout(async () => {
      try {
        setCalc(await api<Calc>("/calc/landed", { method: "POST", body: JSON.stringify(body(warehouse, true)) }));
        setCalcErr(null);
        if (rules) {
          const whs = rules.tiers.flatMap((t) => t.warehouses.map((w) => w.name));
          const res = await Promise.all(
            whs.map((w) =>
              api<Calc>("/calc/landed", { method: "POST", body: JSON.stringify(body(w)) }).then((r) => ({
                name: w,
                landed: r.per_lb.landed_usd_lb,
                roasted: r.per_lb.roasted_usd_lb,
                freight: r.per_lb.freight_usd_lb,
              })),
            ),
          );
          setCompare(res.sort((a, b) => a.landed - b.landed));
        }
      } catch (e) {
        setCalcErr((e as Error).message);
      }
    }, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [price, unit, bagKg, bags, warehouse, freightOverride, shrink, tariff, ocean, oceanBasis, containerBags, rules]);

  const saveRules = async () => {
    if (!rules) return;
    try {
      const r = await api<FreightRules>("/settings/freight", { method: "PUT", body: JSON.stringify(rules) });
      setRules(r);
      setSaved("Saved — applies to newly ingested offers and all calculations.");
    } catch (e) {
      setSaved(`Error: ${(e as Error).message}`);
    }
  };

  const setTier = (i: number, k: "low" | "high" | "default", v: string) => {
    if (!rules) return;
    const tiers = rules.tiers.map((t, j) => (j === i ? { ...t, [k]: Number(v) } : t));
    setRules({ ...rules, tiers });
    setSaved(null);
  };

  const p = calc?.per_lb;
  const o = calc?.order;
  const maxLanded = Math.max(...compare.map((c) => c.landed), 0.01);
  const minLanded = Math.min(...compare.map((c) => c.landed));
  const friction = p ? p.tariff_usd_lb + p.ocean_usd_lb : 0;

  return (
    <>
      <PageHeader
        title="Atlanta landed-cost simulator"
        subtitle="Landed green = spot × (1 + tariff) + ocean surcharge + LTL freight to Atlanta + warehouse/financing fee. True roasted cost = landed ÷ (1 − roast shrinkage)."
      />
      <ErrorNote error={rulesApi.error || calcErr} />

      <div className="grid gap-4 lg:grid-cols-5">
        <div className="space-y-4 lg:col-span-2">
          <section className="card space-y-4">
            <h2 className="card-title">Offer</h2>
            <div className="flex flex-wrap items-end gap-2">
              <label className="text-sm">
                <div className="eyebrow mb-1">Spot price (USD)</div>
                <input className="input mono w-32" type="number" step="0.01" min="0" value={price} onChange={(e) => setPrice(e.target.value)} />
              </label>
              <Segmented
                label="Price basis"
                value={unit}
                onChange={setUnit}
                options={[
                  { value: "lb", label: "per lb" },
                  { value: "kg", label: "per kg" },
                  { value: "bag", label: "per bag" },
                ]}
              />
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <label className="text-sm">
                <div className="eyebrow mb-1">Bag size</div>
                <select className="input" value={bagKg} onChange={(e) => setBagKg(e.target.value)}>
                  <option value="60">60 kg (Brazil, Africa, Asia)</option>
                  <option value="69">69 kg (Central America, Peru)</option>
                  <option value="70">70 kg (Colombia)</option>
                  <option value="35">35 kg</option>
                  <option value="30">30 kg / box</option>
                  <option value="9.98">22 lb box</option>
                </select>
              </label>
              <label className="text-sm">
                <div className="eyebrow mb-1">Bags</div>
                <input className="input mono w-20" type="number" min="1" value={bags} onChange={(e) => setBags(e.target.value)} />
              </label>
            </div>
            <label className="block text-sm">
              <div className="eyebrow mb-1">Warehouse</div>
              <input className="input w-full" list="whs" value={warehouse} onChange={(e) => setWarehouse(e.target.value)} />
              <datalist id="whs">{rules?.tiers.flatMap((t) => t.warehouses.map((w) => <option key={w.name} value={w.name} />))}</datalist>
              {calc && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  <Badge tone={calc.warehouse.matched ? "neutral" : "warn"}>
                    {calc.warehouse.matched ? `${calc.warehouse.tier_label} tier` : "Unrecognised — unknown-warehouse default"}
                  </Badge>
                  {calc.warehouse.southeast && <Badge tone="pos">Southeast</Badge>}
                </div>
              )}
            </label>
            <label className="block text-sm">
              <div className="eyebrow mb-1">LTL freight override ($/lb, optional)</div>
              <input
                className="input mono w-32"
                type="number"
                step="0.01"
                min="0"
                value={freightOverride}
                onChange={(e) => setFreightOverride(e.target.value)}
                placeholder="auto"
              />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <span className="eyebrow">Roast shrinkage</span>
              <input type="range" min={10} max={22} step={0.5} value={shrink} onChange={(e) => setShrink(Number(e.target.value))} className="accent-[var(--accent)]" />
              <span className="mono w-12 font-medium">{shrink.toFixed(1)}%</span>
            </label>
          </section>

          <section className="card space-y-4">
            <div>
              <h2 className="card-title">Tariff & ocean freight friction</h2>
              <p className="mt-0.5 text-xs text-muted">
                For offers quoted before duty or FOB origin. Importer spot prices are usually duty-paid and ex-warehouse — leave at 0 for those.
              </p>
            </div>
            <label className="block text-sm">
              <div className="eyebrow mb-1">Tariff / import duty</div>
              <div className="flex items-center gap-2">
                <input className="input mono w-24 text-right" type="number" step="0.5" min="0" max="200" value={tariff} onChange={(e) => setTariff(e.target.value)} />
                <span className="text-ink2">% of spot value</span>
              </div>
            </label>
            <div className="text-sm">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="eyebrow">Ocean freight surcharge</span>
                <Segmented
                  label="Ocean surcharge basis"
                  size="sm"
                  value={oceanBasis}
                  onChange={setOceanBasis}
                  options={[
                    { value: "container", label: "$/container" },
                    { value: "lb", label: "$/lb" },
                  ]}
                />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <input className="input mono w-28 text-right" type="number" step={oceanBasis === "lb" ? "0.01" : "50"} min="0" value={ocean} onChange={(e) => setOcean(e.target.value)} />
                {oceanBasis === "container" && (
                  <>
                    <span className="text-ink2">per 20′ container of</span>
                    <input className="input mono w-20 text-right" type="number" min="1" value={containerBags} onChange={(e) => setContainerBags(e.target.value)} aria-label="Bags per container" />
                    <span className="text-ink2">bags</span>
                  </>
                )}
              </div>
              {p && p.ocean_usd_lb > 0 && <div className="mono mt-1 text-xs text-muted">= {m.dualText(p.ocean_usd_lb, 4)}</div>}
            </div>
          </section>
        </div>

        <section className="card lg:col-span-3">
          <h2 className="card-title mb-4">Cost build-up</h2>
          {p ? (
            <>
              <div className="mb-5 grid gap-3 sm:grid-cols-3">
                <ResultTile label="Landed green · Atlanta" usdLb={p.landed_usd_lb} accent />
                <ResultTile label={`True roasted · ${(p.shrinkage * 100).toFixed(1)}% shrink`} usdLb={p.roasted_usd_lb} />
                <ResultTile label="Tariff + ocean friction" usdLb={friction} muted={friction === 0} />
              </div>
              <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    <th className="th !pl-0" />
                    <th className="th num">Per unit</th>
                    {o && <th className="th num !pr-0">Order · {o.green_lb.toLocaleString()} lb green</th>}
                  </tr>
                </thead>
                <tbody>
                  {(
                    [
                      ["Spot (green)", p.spot_usd_lb, o?.green_cost_usd, true],
                      [`Tariff (${(p.tariff_pct * 100).toFixed(1)}%)`, p.tariff_usd_lb, o?.tariff_usd, p.tariff_usd_lb > 0],
                      ["Ocean freight surcharge", p.ocean_usd_lb, o?.ocean_usd, p.ocean_usd_lb > 0],
                      ["LTL freight to Atlanta", p.freight_usd_lb, o?.freight_usd, true],
                      ["Warehouse / financing fee", p.fee_usd_lb, o?.fees_usd, true],
                    ] as [string, number, number | undefined, boolean][]
                  ).map(([label, lb, tot, on]) => (
                    <tr key={label} className={`border-t border-border ${on ? "" : "opacity-50"}`}>
                      <td className="td !pl-0 text-ink2">{label}</td>
                      <td className="td num">
                        <Price usdLb={lb} digits={4} layout="stack" />
                      </td>
                      {o && <td className="td num !pr-0">{usd(tot ?? 0)}</td>}
                    </tr>
                  ))}
                  <tr className="border-t-2 border-[var(--border-strong)]">
                    <td className="td !pl-0 font-semibold">Landed green cost</td>
                    <td className="td num">
                      <Price usdLb={p.landed_usd_lb} digits={4} layout="stack" />
                    </td>
                    {o && <td className="td num !pr-0 font-semibold">{usd(o.landed_total_usd)}</td>}
                  </tr>
                  <tr className="border-t border-border">
                    <td className="td !pl-0 font-semibold">True roasted cost</td>
                    <td className="td num">
                      <Price usdLb={p.roasted_usd_lb} digits={4} layout="stack" />
                    </td>
                    {o && <td className="td num !pr-0 text-ink2">{o.roasted_lb.toLocaleString()} lb roasted</td>}
                  </tr>
                </tbody>
              </table>
              </div>

              {compare.length > 0 && (
                <>
                  <h3 className="card-title mb-1 mt-6">Same offer landed from each warehouse</h3>
                  <p className="mb-3 text-xs text-muted">
                    Landed cost {m.suffix}; the label shows the LTL freight to Atlanta. Cheapest: {compare[0].name}.
                  </p>
                  <ul className="space-y-1.5">
                    {compare.map((c) => {
                      const sel = c.name === calc?.warehouse.warehouse;
                      const lo = minLanded * 0.97;
                      return (
                        <li
                          key={c.name}
                          className="grid grid-cols-[130px_1fr_150px] items-center gap-3 text-sm"
                          title={`${c.name}: freight ${m.dualText(c.freight)}, landed ${m.dualText(c.landed)}, roasted ${m.dualText(c.roasted)}`}
                        >
                          <span className={`truncate ${sel ? "font-semibold text-ink" : "text-ink2"}`}>{c.name}</span>
                          <span className="relative h-2.5 rounded-full bg-surface2">
                            <span
                              className="absolute inset-y-0 left-0 rounded-full"
                              style={{
                                width: `${Math.max(4, ((c.landed - lo) / (maxLanded - lo)) * 100)}%`,
                                background: sel ? "var(--accent)" : "var(--seq-3)",
                              }}
                            />
                          </span>
                          <span className="flex items-baseline justify-end gap-2">
                            <span className="mono text-[11px] text-muted">+{m.fmt(c.freight)}</span>
                            <Price usdLb={c.landed} size="sm" />
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </>
              )}
            </>
          ) : (
            <div className="text-sm text-muted">Enter a price to see the breakdown.</div>
          )}
        </section>
      </div>

      <section className="card mt-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="card-title">Freight ruleset</h2>
            <p className="text-xs text-muted">Palletized LTL estimates into Atlanta (303xx). Offers use each tier’s default; low/high are the expected range.</p>
          </div>
          <div className="flex items-center gap-2">
            {saved && <span className="text-sm text-ink2">{saved}</span>}
            <button className="btn-primary" onClick={saveRules} disabled={!rules}>
              Save rules
            </button>
          </div>
        </div>
        {rules && (
          <>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    <th className="th">Tier</th>
                    <th className="th">Warehouses</th>
                    <th className="th num">Low</th>
                    <th className="th num">Default</th>
                    <th className="th num">High</th>
                  </tr>
                </thead>
                <tbody>
                  {rules.tiers.map((t, i) => (
                    <tr key={t.id} className="border-t border-border">
                      <td className="td font-medium">
                        {t.label} {t.southeast && <Badge tone="pos">SE</Badge>}
                      </td>
                      <td className="td whitespace-normal text-sm text-ink2">{t.warehouses.map((w) => w.name).join(" · ")}</td>
                      {(["low", "default", "high"] as const).map((key) => (
                        <td key={key} className="td num">
                          <input
                            className="input mono w-20 text-right"
                            type="number"
                            step="0.01"
                            min="0"
                            value={t[key]}
                            onChange={(e) => setTier(i, key, e.target.value)}
                            aria-label={`${t.label} ${key}`}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-4 flex flex-wrap gap-x-6 gap-y-3 text-sm">
              <RuleInput label="Warehouse/financing fee $/lb" value={rules.warehouse_fee_usd_lb} step="0.01" onChange={(v) => { setRules({ ...rules, warehouse_fee_usd_lb: v }); setSaved(null); }} />
              <RuleInput label="Unknown warehouse freight $/lb" value={rules.unknown_warehouse_freight_usd_lb} step="0.01" onChange={(v) => { setRules({ ...rules, unknown_warehouse_freight_usd_lb: v }); setSaved(null); }} />
              <RuleInput label="Default shrinkage" value={rules.default_shrinkage} step="0.005" onChange={(v) => { setRules({ ...rules, default_shrinkage: v }); setSaved(null); }} />
              <RuleInput label="Default tariff (fraction)" value={rules.tariff_pct ?? 0} step="0.005" onChange={(v) => { setRules({ ...rules, tariff_pct: v }); setSaved(null); }} />
              <RuleInput label="Default ocean surcharge $/lb" value={rules.ocean_surcharge_usd_lb ?? 0} step="0.01" onChange={(v) => { setRules({ ...rules, ocean_surcharge_usd_lb: v }); setSaved(null); }} />
            </div>
            <p className="mt-2 text-xs text-muted">Default tariff and ocean surcharge are added to the landed cost of newly ingested offers.</p>
          </>
        )}
      </section>
    </>
  );
}

function ResultTile({ label, usdLb, accent, muted }: { label: string; usdLb: number; accent?: boolean; muted?: boolean }) {
  return (
    <div className={`rounded-xl border p-4 ${accent ? "border-[var(--accent-ring)] bg-accent-soft" : "border-border bg-surface"} ${muted ? "opacity-60" : ""}`}>
      <div className="eyebrow">{label}</div>
      <div className="mt-2">
        <Price usdLb={usdLb} size="lg" layout="stack" digits={2} className="!items-start" />
      </div>
    </div>
  );
}

function RuleInput({ label, value, step, onChange }: { label: string; value: number; step: string; onChange: (v: number) => void }) {
  return (
    <label className="flex items-center gap-2">
      <span className="text-ink2">{label}</span>
      <input className="input mono w-20 text-right" type="number" step={step} min="0" value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}
