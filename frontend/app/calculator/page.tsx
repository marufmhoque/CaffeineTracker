"use client";

import { useEffect, useState } from "react";
import { ErrorNote, PageHeader, Segmented } from "@/components/ui";
import { api, useApi, type FreightRules } from "@/lib/api";

type Calc = {
  warehouse: { warehouse: string; tier_id: string; tier_label: string; freight_usd_lb: number; southeast: boolean; matched: boolean };
  per_lb: { spot_usd_lb: number; freight_usd_lb: number; fee_usd_lb: number; landed_usd_lb: number; shrinkage: number; roasted_usd_lb: number };
  per_kg: { spot_usd_kg: number; freight_usd_kg: number; fee_usd_kg: number; landed_usd_kg: number; roasted_usd_kg: number };
  order?: { green_lb: number; roasted_lb: number; green_cost_usd: number; freight_usd: number; fees_usd: number; landed_total_usd: number };
};

const money = (v: number, d = 2) => `$${v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;

export default function Calculator() {
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
  const [calc, setCalc] = useState<Calc | null>(null);
  const [calcErr, setCalcErr] = useState<string | null>(null);
  const [compare, setCompare] = useState<{ name: string; landed: number; roasted: number; freight: number }[]>([]);

  useEffect(() => {
    if (rulesApi.data) {
      setRules(rulesApi.data);
      setShrink(Math.round(rulesApi.data.default_shrinkage * 1000) / 10);
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
            whs.map((w) => api<Calc>("/calc/landed", { method: "POST", body: JSON.stringify(body(w)) }).then((r) => ({ name: w, landed: r.per_lb.landed_usd_lb, roasted: r.per_lb.roasted_usd_lb, freight: r.per_lb.freight_usd_lb }))),
          );
          setCompare(res.sort((a, b) => a.landed - b.landed));
        }
      } catch (e) {
        setCalcErr((e as Error).message);
      }
    }, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [price, unit, bagKg, bags, warehouse, freightOverride, shrink, rules]);

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
  const k = calc?.per_kg;
  const maxFreight = Math.max(...compare.map((c) => c.freight), 0.01);

  return (
    <>
      <PageHeader
        title="Atlanta landed-cost simulator"
        subtitle="Landed green = spot + LTL freight to Atlanta + warehouse/financing fee. True roasted cost = landed ÷ (1 − roast shrinkage)."
      />
      <ErrorNote error={rulesApi.error || calcErr} />

      <div className="grid gap-4 lg:grid-cols-5">
        <section className="card space-y-3 lg:col-span-2">
          <h2 className="font-medium">Offer</h2>
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-sm">
              <div className="mb-1 text-ink2">Spot price (USD)</div>
              <input className="input w-32" type="number" step="0.01" min="0" value={price} onChange={(e) => setPrice(e.target.value)} />
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
              <div className="mb-1 text-ink2">Bag size</div>
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
              <div className="mb-1 text-ink2">Bags</div>
              <input className="input w-20" type="number" min="1" value={bags} onChange={(e) => setBags(e.target.value)} />
            </label>
          </div>
          <label className="block text-sm">
            <div className="mb-1 text-ink2">Warehouse</div>
            <input className="input w-full" list="whs" value={warehouse} onChange={(e) => setWarehouse(e.target.value)} />
            <datalist id="whs">
              {rules?.tiers.flatMap((t) => t.warehouses.map((w) => <option key={w.name} value={w.name} />))}
            </datalist>
            {calc && (
              <div className="mt-1 text-xs text-muted">
                {calc.warehouse.matched ? `${calc.warehouse.tier_label} tier` : "Unrecognised — using the unknown-warehouse default"}
                {calc.warehouse.southeast && " · Southeast"}
              </div>
            )}
          </label>
          <label className="block text-sm">
            <div className="mb-1 text-ink2">Freight override ($/lb, optional — e.g. from an actual LTL quote)</div>
            <input className="input w-32" type="number" step="0.01" min="0" value={freightOverride} onChange={(e) => setFreightOverride(e.target.value)} placeholder="auto" />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-ink2">Roast shrinkage</span>
            <input type="range" min={10} max={22} step={0.5} value={shrink} onChange={(e) => setShrink(Number(e.target.value))} />
            <span className="num w-12 font-medium">{shrink.toFixed(1)}%</span>
          </label>
        </section>

        <section className="card lg:col-span-3">
          <h2 className="mb-3 font-medium">Cost build-up</h2>
          {p && k ? (
            <>
              <table className="w-full">
                <thead>
                  <tr>
                    <th className="th" />
                    <th className="th num">$/lb</th>
                    <th className="th num">$/kg</th>
                    {calc?.order && <th className="th num">Order ({calc.order.green_lb.toLocaleString()} lb)</th>}
                  </tr>
                </thead>
                <tbody>
                  {[
                    ["Spot (green)", p.spot_usd_lb, k.spot_usd_kg, calc?.order?.green_cost_usd],
                    ["LTL freight to Atlanta", p.freight_usd_lb, k.freight_usd_kg, calc?.order?.freight_usd],
                    ["Warehouse / financing fee", p.fee_usd_lb, k.fee_usd_kg, calc?.order?.fees_usd],
                  ].map(([label, lb, kg, tot]) => (
                    <tr key={label as string} className="border-t border-[var(--grid)]">
                      <td className="td text-ink2">{label}</td>
                      <td className="td num">{money(lb as number, 4)}</td>
                      <td className="td num">{money(kg as number, 4)}</td>
                      {calc?.order && <td className="td num">{money(tot as number)}</td>}
                    </tr>
                  ))}
                  <tr className="border-t-2 border-[var(--axis)]">
                    <td className="td font-semibold">Landed green cost</td>
                    <td className="td num font-semibold">{money(p.landed_usd_lb, 4)}</td>
                    <td className="td num font-semibold">{money(k.landed_usd_kg, 4)}</td>
                    {calc?.order && <td className="td num font-semibold">{money(calc.order.landed_total_usd)}</td>}
                  </tr>
                  <tr className="border-t border-[var(--grid)]">
                    <td className="td font-semibold">True roasted cost ({(p.shrinkage * 100).toFixed(1)}% shrink)</td>
                    <td className="td num text-lg font-semibold">{money(p.roasted_usd_lb, 4)}</td>
                    <td className="td num font-semibold">{money(k.roasted_usd_kg, 4)}</td>
                    {calc?.order && <td className="td num text-ink2">{calc.order.roasted_lb.toLocaleString()} lb roasted</td>}
                  </tr>
                </tbody>
              </table>

              {compare.length > 0 && (
                <>
                  <h3 className="mb-1 mt-5 text-sm font-medium">Same offer from each warehouse</h3>
                  <p className="mb-2 text-xs text-muted">Bar = freight to Atlanta $/lb; right = landed $/lb.</p>
                  <ul className="space-y-1.5">
                    {compare.map((c) => (
                      <li key={c.name} className="grid grid-cols-[120px_1fr_44px_56px] items-center gap-2 text-sm" title={`${c.name}: freight ${money(c.freight)}, landed ${money(c.landed)}, roasted ${money(c.roasted)}`}>
                        <span className={`truncate ${c.name === calc?.warehouse.warehouse ? "font-medium text-ink" : "text-ink2"}`}>{c.name}</span>
                        <span className="h-3">
                          <span
                            className="block h-3 rounded-r"
                            style={{ width: `${(c.freight / maxFreight) * 100}%`, background: c.name === calc?.warehouse.warehouse ? "var(--series-1)" : "var(--seq-2)" }}
                          />
                        </span>
                        <span className="num text-xs text-muted">{money(c.freight)}</span>
                        <span className="num">{money(c.landed)}</span>
                      </li>
                    ))}
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
            <h2 className="font-medium">Freight ruleset</h2>
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
                  <tr key={t.id} className="border-t border-[var(--grid)]">
                    <td className="td font-medium">
                      {t.label}
                      {t.southeast && <span className="ml-1.5 rounded bg-[var(--series-3)] px-1 py-0.5 text-[10px] font-semibold text-white">SE</span>}
                    </td>
                    <td className="td whitespace-normal text-sm text-ink2">{t.warehouses.map((w) => w.name).join(" · ")}</td>
                    {(["low", "default", "high"] as const).map((key) => (
                      <td key={key} className="td num">
                        <input className="input w-20 text-right" type="number" step="0.01" min="0" value={t[key]} onChange={(e) => setTier(i, key, e.target.value)} aria-label={`${t.label} ${key}`} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-3 flex flex-wrap gap-4 text-sm">
              <label className="flex items-center gap-2">
                <span className="text-ink2">Warehouse/financing fee $/lb</span>
                <input className="input w-20 text-right" type="number" step="0.01" min="0" value={rules.warehouse_fee_usd_lb} onChange={(e) => { setRules({ ...rules, warehouse_fee_usd_lb: Number(e.target.value) }); setSaved(null); }} />
              </label>
              <label className="flex items-center gap-2">
                <span className="text-ink2">Unknown warehouse freight $/lb</span>
                <input className="input w-20 text-right" type="number" step="0.01" min="0" value={rules.unknown_warehouse_freight_usd_lb} onChange={(e) => { setRules({ ...rules, unknown_warehouse_freight_usd_lb: Number(e.target.value) }); setSaved(null); }} />
              </label>
              <label className="flex items-center gap-2">
                <span className="text-ink2">Default shrinkage</span>
                <input className="input w-20 text-right" type="number" step="0.005" min="0" max="0.5" value={rules.default_shrinkage} onChange={(e) => { setRules({ ...rules, default_shrinkage: Number(e.target.value) }); setSaved(null); }} />
              </label>
            </div>
          </>
        )}
      </section>
    </>
  );
}
