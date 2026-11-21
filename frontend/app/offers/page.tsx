"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Badge, Empty, ErrorNote, PageHeader, Price, SpeciesToggle, type Species } from "@/components/ui";
import { useApi, type Offer } from "@/lib/api";
import { useMoney } from "@/lib/prefs";

type Facets = { importers: string[]; processes: string[]; countries: string[]; count: number };
type SortKey = "landed_usd_lb" | "spot_usd_lb" | "roasted_usd_lb" | "sca_score" | "value";

export default function Offers() {
  const m = useMoney();
  const facets = useApi<Facets>("/offers/facets");
  const [country, setCountry] = useState("");
  const [process, setProcess] = useState("");
  const [importer, setImporter] = useState("");
  const [minScore, setMinScore] = useState("");
  const [southeast, setSoutheast] = useState(false);
  const [shrink, setShrink] = useState(15);
  const [sort, setSort] = useState<SortKey>("landed_usd_lb");
  const [species, setSpecies] = useState<Species>("All");

  useEffect(() => {
    const c = new URLSearchParams(window.location.search).get("country");
    if (c) setCountry(c.toUpperCase());
  }, []);

  const qs = new URLSearchParams();
  if (country) qs.set("country", country);
  if (process) qs.set("process", process);
  if (importer) qs.set("importer", importer);
  if (minScore) qs.set("min_score", minScore);
  if (southeast) qs.set("southeast", "true");
  if (species !== "All") qs.set("species", species);
  qs.set("shrinkage", String(shrink / 100));
  const { data, error, loading } = useApi<{ shrinkage: number; offers: Offer[] }>(`/offers?${qs}`);

  const rows = useMemo(() => {
    const list = [...(data?.offers ?? [])];
    // Value per point: landed $/lb per SCA point above 80 (lower = more cup for the money).
    const vpp = (o: Offer) => (o.sca_score && o.sca_score > 80 && o.landed_usd_lb ? o.landed_usd_lb / (o.sca_score - 80) : Infinity);
    list.sort((a, b) => {
      if (sort === "sca_score") return (b.sca_score ?? 0) - (a.sca_score ?? 0);
      if (sort === "value") return vpp(a) - vpp(b);
      return ((a[sort] as number) ?? Infinity) - ((b[sort] as number) ?? Infinity);
    });
    return list;
  }, [data, sort]);

  const countryName = (iso: string) => data?.offers.find((o) => o.country_iso === iso)?.country ?? iso;

  return (
    <>
      <PageHeader
        title="Live spot offers"
        subtitle="Current importer offerings with estimated LTL freight to Atlanta, landed green cost and true roasted cost. Southeast warehouses (Savannah, Charleston, Jacksonville) save cross-country freight."
      >
        <SpeciesToggle value={species} onChange={setSpecies} />
      </PageHeader>
      <ErrorNote error={error} />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select className="input" value={country} onChange={(e) => setCountry(e.target.value)} aria-label="Country">
          <option value="">All origins</option>
          {facets.data?.countries.map((c) => (
            <option key={c} value={c}>
              {countryName(c)}
            </option>
          ))}
        </select>
        <select className="input" value={process} onChange={(e) => setProcess(e.target.value)} aria-label="Process">
          <option value="">Any process</option>
          {facets.data?.processes.map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
        <select className="input" value={importer} onChange={(e) => setImporter(e.target.value)} aria-label="Importer">
          <option value="">All importers</option>
          {facets.data?.importers.map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
        <select className="input" value={minScore} onChange={(e) => setMinScore(e.target.value)} aria-label="Minimum score">
          <option value="">Any score</option>
          {[84, 85, 86, 87, 88, 89, 90].map((s) => (
            <option key={s} value={s}>
              {s}+ SCA
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-sm text-ink2">
          <input type="checkbox" checked={southeast} onChange={(e) => setSoutheast(e.target.checked)} /> Southeast warehouses only
        </label>
        <label className="ml-auto flex items-center gap-2 text-sm text-ink2">
          Roast shrinkage
          <input type="range" min={10} max={22} step={0.5} value={shrink} onChange={(e) => setShrink(Number(e.target.value))} aria-label="Roast shrinkage percent" />
          <span className="num w-12 font-medium text-ink">{shrink.toFixed(1)}%</span>
        </label>
        <select className="input" value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort by">
          <option value="landed_usd_lb">Sort: landed cost</option>
          <option value="spot_usd_lb">Sort: spot price</option>
          <option value="roasted_usd_lb">Sort: roasted cost</option>
          <option value="sca_score">Sort: score</option>
          <option value="value">Sort: value per point</option>
        </select>
      </div>

      {rows.length === 0 && !loading ? (
        <Empty title="No current spot offers">
          Run the importer scrapers, or drop a spot sheet (CSV / XLSX / PDF) on the <Link className="link" href="/data">Data Sources</Link> page.
        </Empty>
      ) : (
        <div className={`card overflow-x-auto p-0 transition-opacity ${loading ? "opacity-60" : ""}`}>
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Lot</th>
                <th className="th">Origin</th>
                <th className="th">Process / variety</th>
                <th className="th num">Score</th>
                <th className="th">Warehouse</th>
                <th className="th num">Spot</th>
                <th className="th num">+ Freight</th>
                <th className="th num">Landed ATL</th>
                <th className="th num">Roasted</th>
                <th className="th num">Bags</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.id} className="border-t border-border align-top hover:bg-[var(--surface-hover)]">
                  <td className="td">
                    <div className="w-[260px] whitespace-normal">
                    {o.raw_url ? (
                      <a href={o.raw_url} target="_blank" rel="noreferrer" className="font-medium hover:underline">
                        {o.lot_name}
                      </a>
                    ) : (
                      <span className="font-medium">{o.lot_name}</span>
                    )}
                    <div className="text-xs text-muted">
                      {o.importer}
                      {o.offer_date ? ` · ${o.offer_date}` : ""}
                      {o.bag_weight_kg ? ` · ${Math.round(o.bag_weight_kg * 10) / 10} kg` : ""}
                    </div>
                    </div>
                  </td>
                  <td className="td">
                    {o.country_iso ? (
                      <Link href={`/trends?country=${o.country_iso}${o.process ? `&process=${encodeURIComponent(o.process)}` : ""}`} className="hover:underline">
                        {o.country}
                      </Link>
                    ) : (
                      "—"
                    )}
                    {o.origin_region && <div className="max-w-[160px] truncate text-xs text-muted">{o.origin_region}</div>}
                  </td>
                  <td className="td">
                    <div className="w-[180px] whitespace-normal">
                      {o.process ?? "—"}
                      {o.variety && <div className="text-xs text-muted">{o.variety}</div>}
                      {o.species && o.species !== "Arabica" && (
                        <div className="mt-1">
                          <Badge tone="accent">{o.species}</Badge>
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="td num">{o.sca_score ?? "—"}</td>
                  <td className="td">
                    <span className="inline-flex items-center gap-1.5">
                      {o.is_southeast && (
                        <Badge tone="pos" title="Southeast warehouse">
                          SE
                        </Badge>
                      )}
                      {o.warehouse_location ?? <span className="text-muted">Unknown</span>}
                    </span>
                  </td>
                  <td className="td num">
                    <Price usdLb={o.spot_usd_lb} layout="stack" />
                  </td>
                  <td className="td num text-ink2">
                    {m.fmt(o.freight_usd_lb)}
                    <span className="text-[11px] text-muted">{m.suffix}</span>
                  </td>
                  <td className="td num">
                    <Price usdLb={o.landed_usd_lb} layout="stack" />
                  </td>
                  <td className="td num">
                    <Price usdLb={o.roasted_usd_lb} layout="stack" />
                  </td>
                  <td className="td num">{o.bags_available ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-2 text-xs text-muted">
        Landed = spot × (1 + tariff) + ocean surcharge + LTL freight to Atlanta by warehouse tier + warehouse/financing fee. Roasted = landed ÷ (1 − shrinkage). Edit freight
        tiers on the <Link className="underline" href="/calculator">Landed Cost</Link> page. {rows.length} offers.
      </p>
    </>
  );
}
