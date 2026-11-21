"use client";

import { useMemo, useState } from "react";
import OriginDrawer from "@/components/OriginDrawer";
import { Delta, ErrorNote, HarvestBadge, PageHeader, Price, Segmented, SpeciesToggle, type Species } from "@/components/ui";
import WorldMap, { type MapMetric } from "@/components/WorldMap";
import { useApi, type Country, type Matrix } from "@/lib/api";
import { MONTHS, useMoney } from "@/lib/prefs";

const REGIONS = ["Central America & Mexico", "South America", "Caribbean", "East & Central Africa", "West Africa", "Asia & Oceania"];
type SortKey = "name" | "spot" | "diff" | "trade" | "chg7" | "chg30" | "yoy";

const SORTERS: Record<SortKey, (c: Country) => number | string | null> = {
  name: (c) => c.name,
  spot: (c) => c.spot_avg_usd_lb,
  diff: (c) => c.diff_vs_c_usd_lb,
  trade: (c) => c.trade_unit_usd_lb,
  chg7: (c) => c.chg_7d_pct,
  chg30: (c) => c.chg_30d_pct,
  yoy: (c) => c.chg_yoy_pct,
};

function HarvestStrip({ main, fly }: { main: number[]; fly: number[] }) {
  const now = new Date().getMonth() + 1;
  return (
    <div className="flex gap-px" aria-label={`Main harvest months ${main.join(", ")}${fly.length ? `; fly crop ${fly.join(", ")}` : ""}`}>
      {MONTHS.map((l, i) => {
        const mo = i + 1;
        const bg = main.includes(mo) ? "var(--accent)" : fly.includes(mo) ? "var(--accent-ring)" : "var(--surface-2)";
        return (
          <span
            key={i}
            title={`${l}: ${main.includes(mo) ? "main harvest" : fly.includes(mo) ? "fly crop" : "—"}`}
            className={`flex h-4 w-3.5 items-center justify-center rounded-[3px] text-[9px] ${mo === now ? "ring-1 ring-[var(--text-primary)]" : ""}`}
            style={{ background: bg, color: main.includes(mo) ? "#fff" : "var(--text-muted)" }}
          >
            {l}
          </span>
        );
      })}
    </div>
  );
}

export default function Origins() {
  const m = useMoney();
  const [species, setSpecies] = useState<Species>("All");
  const [selected, setSelected] = useState<string | null>(null);
  const { data, error } = useApi<Matrix>(`/matrix?species=${species}`);
  const [metric, setMetric] = useState<MapMetric>("price");
  const [q, setQ] = useState("");
  const [region, setRegion] = useState("all");
  const [harvest, setHarvest] = useState("all");
  const [onlyPriced, setOnlyPriced] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "name", dir: 1 });

  const rows = data?.countries ?? [];
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return rows.filter(
      (c) =>
        (!s || c.name.toLowerCase().includes(s) || c.iso_code.toLowerCase() === s) &&
        (region === "all" || c.region === region) &&
        (harvest === "all" || c.harvest_status === harvest) &&
        (!onlyPriced || c.spot_avg_usd_lb !== null),
    );
  }, [rows, q, region, harvest, onlyPriced]);

  const grouped = useMemo(() => {
    const cmp = (a: Country, b: Country) => {
      const va = SORTERS[sort.key](a);
      const vb = SORTERS[sort.key](b);
      if (va === null && vb === null) return 0;
      if (va === null) return 1;
      if (vb === null) return -1;
      return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir;
    };
    return REGIONS.map((r) => ({ region: r, rows: filtered.filter((c) => c.region === r).sort(cmp) })).filter((g) => g.rows.length);
  }, [filtered, sort]);

  const bench = data?.benchmark === "robusta" ? "ICE Robusta" : "ICE “C”";

  const H = ({ k, children, num }: { k: SortKey; children: React.ReactNode; num?: boolean }) => (
    <th className={`th ${num ? "num" : ""}`} aria-sort={sort.key === k ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button className="uppercase hover:text-ink" onClick={() => setSort((s) => ({ key: k, dir: s.key === k ? (s.dir === 1 ? -1 : 1) : k === "name" ? 1 : -1 }))}>
        {children} {sort.key === k ? (sort.dir === 1 ? "↑" : "↓") : ""}
      </button>
    </th>
  );

  return (
    <>
      <PageHeader
        title="Global origin matrix"
        subtitle={`Every coffee-producing country: live spot averages, customs import unit values, differential vs ${bench} (${
          data?.benchmark_usd_lb != null ? m.dualText(data.benchmark_usd_lb, 4) : "not loaded"
        }) and harvest status. Click a country for its lots and outlook.`}
      >
        <SpeciesToggle value={species} onChange={setSpecies} />
      </PageHeader>
      <ErrorNote error={error} />

      <section className="card mb-4">
        <div className="mb-2 flex justify-end">
          <Segmented
            size="sm"
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
        {rows.length > 0 && <WorldMap rows={rows} metric={metric} onSelect={setSelected} selected={selected} />}
      </section>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input className="input w-48" placeholder="Search country…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search country" />
        <select className="input" value={region} onChange={(e) => setRegion(e.target.value)} aria-label="Region">
          <option value="all">All regions</option>
          {REGIONS.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <select className="input" value={harvest} onChange={(e) => setHarvest(e.target.value)} aria-label="Harvest status">
          <option value="all">Any harvest status</option>
          {["In Harvest", "Fly Crop", "Main Crop Shipping", "Off-Season"].map((h) => (
            <option key={h}>{h}</option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-sm text-ink2">
          <input type="checkbox" checked={onlyPriced} onChange={(e) => setOnlyPriced(e.target.checked)} /> Live spot offers only
        </label>
        <span className="ml-auto text-sm text-muted">{filtered.length} countries</span>
      </div>

      <div className="card overflow-x-auto p-0">
        <table className="w-full">
          <thead className="sticky top-0 bg-surface">
            <tr>
              <H k="name">Country</H>
              <H k="spot" num>Spot avg</H>
              <H k="diff" num>Diff vs {data?.benchmark === "robusta" ? "Robusta" : "“C”"}</H>
              <H k="trade" num>Customs unit value</H>
              <H k="chg7" num>7d</H>
              <H k="chg30" num>30d</H>
              <H k="yoy" num>YoY</H>
              <th className="th">Harvest</th>
              <th className="th">Calendar</th>
            </tr>
          </thead>
          {grouped.map((g) => (
            <tbody key={g.region}>
              <tr>
                <td colSpan={9} className="eyebrow bg-surface2 px-3 py-2 !text-ink2">
                  {g.region} <span className="font-normal text-muted">· {g.rows.length}</span>
                </td>
              </tr>
              {g.rows.map((c) => (
                <tr key={c.iso_code} className="border-t border-border hover:bg-[var(--surface-hover)]">
                  <td className="td">
                    <button onClick={() => setSelected(c.iso_code)} className="font-medium hover:underline">
                      {c.name}
                    </button>
                    <div className="text-xs text-muted">
                      {c.species_mix} · {c.currency_code}
                    </div>
                  </td>
                  <td className="td num">
                    <Price usdLb={c.spot_avg_usd_lb} layout="stack" />
                    {c.spot_offer_count > 0 && (
                      <div className="text-xs text-muted">
                        {c.spot_offer_count} {c.spot_basis === "small packs" ? "small-pack" : ""} offers
                        {c.southeast_offer_count ? ` · ${c.southeast_offer_count} SE` : ""}
                      </div>
                    )}
                  </td>
                  <td className="td num">
                    <Price usdLb={c.diff_vs_c_usd_lb} layout="stack" signed />
                    {c.diff_basis && <div className="text-xs text-muted">{c.diff_basis === "spot" ? "spot" : "customs"}</div>}
                  </td>
                  <td className="td num">
                    <Price usdLb={c.trade_unit_usd_lb} layout="stack" />
                    {c.trade_period && (
                      <div className="text-xs text-muted">
                        {c.trade_freq === "A" ? `${c.trade_period.slice(0, 4)} annual` : `3 mo to ${c.trade_period.slice(0, 7)}`} · {c.trade_reporters.join("/")}
                      </div>
                    )}
                  </td>
                  <td className="td num">
                    <Delta value={c.chg_7d_pct} />
                  </td>
                  <td className="td num">
                    <Delta value={c.chg_30d_pct} />
                    {c.change_basis === "trade" && <div className="text-xs text-muted">MoM customs</div>}
                  </td>
                  <td className="td num">
                    <Delta value={c.chg_yoy_pct} />
                  </td>
                  <td className="td">
                    <HarvestBadge status={c.harvest_status} />
                  </td>
                  <td className="td">
                    <HarvestStrip main={c.main_harvest_months} fly={c.fly_harvest_months} />
                  </td>
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>
      <p className="mt-2 text-xs text-muted">
        Change pills: ▲ emerald = price up, ▼ rose = price down. Customs unit values are monthly where the importing market reports real weights (else
        annual) and cover both species — a broad market signal, not a specialty price. Species filters keep origins growing that species and average only its
        lots (untagged lots count as the origin&apos;s main species).
      </p>
      <OriginDrawer iso={selected} onClose={() => setSelected(null)} />
    </>
  );
}
