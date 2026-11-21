"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Badge, Delta, Empty, HarvestBadge, Price, RiskBadge, Segmented } from "@/components/ui";
import { useApi, type CountryDetail, type Lot } from "@/lib/api";
import { MONTHS, useMoney } from "@/lib/prefs";

type Sort = "landed" | "score";

/** Right-hand slide-over with an origin's overview and its currently available lots. */
export default function OriginDrawer({ iso, onClose }: { iso: string | null; onClose: () => void }) {
  const detail = useApi<CountryDetail>(iso ? `/countries/${iso}/detail` : null);
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    if (!iso) return;
    opener.current = document.activeElement;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && panel.current) {
        // Keep focus inside the drawer.
        const f = panel.current.querySelectorAll<HTMLElement>("a[href],button:not([disabled]),select,input,[tabindex='0']");
        if (!f.length) return;
        const first = f[0];
        const last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
      (opener.current as HTMLElement | null)?.focus?.();
    };
  }, [iso, onClose]);

  if (!iso) return null;
  const d = detail.data?.iso_code === iso ? detail.data : null;

  return (
    <div className="fixed inset-0 z-50">
      <div className="drawer-backdrop absolute inset-0 bg-[rgba(9,9,11,0.32)] backdrop-blur-[2px]" onClick={onClose} aria-hidden />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={d ? `${d.name} origin details` : "Origin details"}
        tabIndex={-1}
        className="drawer-panel absolute inset-y-0 right-0 flex w-full max-w-[560px] flex-col border-l border-border bg-page shadow-pop outline-none"
      >
        <div className="flex items-start justify-between gap-3 border-b border-border bg-surface px-5 py-4">
          <div>
            <div className="eyebrow">{d?.region ?? "Origin"}</div>
            <h2 className="mt-0.5 text-xl font-semibold tracking-tight">{d?.name ?? iso}</h2>
            {d && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                <HarvestBadge status={d.harvest_status} />
                <RiskBadge risk={d.weather_risk} />
                {d.crop?.cycle && (
                  <Badge tone={d.crop.cycle === "On-year" ? "pos" : "warn"} title={`Derived from USDA ${d.crop.cycle_basis}`}>
                    {d.crop.cycle} {d.crop.market_year}
                  </Badge>
                )}
              </div>
            )}
          </div>
          <button onClick={onClose} className="btn rounded-full px-2.5" aria-label="Close details">
            ✕
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {detail.error && <p className="text-sm text-neg">{detail.error}</p>}
          {!d && !detail.error && <div className="h-40 animate-pulse rounded-xl bg-surface2" />}
          {d && <Body d={d} />}
        </div>
      </div>
    </div>
  );
}

function Body({ d }: { d: CountryDetail }) {
  const m = useMoney();
  const p = d.prices;
  const headline = p?.spot_avg_usd_lb ?? p?.trade_unit_usd_lb ?? null;
  return (
    <>
      <section className="card">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <div className="eyebrow">{p?.spot_avg_usd_lb != null ? "Spot average" : "Customs unit value"}</div>
            <div className="mt-2">
              <Price usdLb={headline} size="lg" layout="stack" className="!items-start" />
            </div>
            {p && (
              <div className="mt-2 flex flex-wrap gap-2">
                <Delta value={p.chg_30d_pct} label="30d" />
              </div>
            )}
          </div>
          <div>
            <div className="eyebrow">Differential vs benchmark</div>
            <div className="mt-2">
              <Price usdLb={p?.diff_vs_c_usd_lb} size="lg" layout="stack" signed className="!items-start" />
            </div>
            {d.typical_diff_usd_lb[0] != null && (
              <div className="mt-2 text-xs text-muted">
                Typical {m.dual(d.typical_diff_usd_lb[0], 2, true).primary.replace(m.suffix, "")} to{" "}
                {m.dual(d.typical_diff_usd_lb[1], 2, true).primary}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="card space-y-4">
        <div>
          <div className="card-title">Harvest window</div>
          <HarvestStrip main={d.main_harvest_months} fly={d.fly_harvest_months} />
        </div>
        <div>
          <div className="flex items-baseline justify-between">
            <div className="card-title">Species</div>
            <span className="text-[11px] text-muted">{d.species_share_basis}</span>
          </div>
          <SpeciesBar share={d.species_share} />
        </div>
        {d.crop && (
          <div className="grid grid-cols-3 gap-3 border-t border-border pt-4 text-sm">
            <Stat label={`USDA ${d.crop.market_year}`} value={`${(d.crop.production_k_bags / 1000).toFixed(1)}M bags`} />
            <Stat label="vs last year" value={<Delta value={d.crop.yoy_pct} />} />
            <Stat label="vs 5-yr avg" value={<Delta value={d.crop.vs_5y_avg_pct} />} />
          </div>
        )}
      </section>

      {d.weather.length > 0 && (
        <section className="card">
          <div className="card-title mb-2">Growing belts</div>
          <ul className="divide-y divide-[var(--border)]">
            {d.weather.map((w) => (
              <li key={w.belt} className="py-2.5 first:pt-0 last:pb-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">
                    {w.belt} <span className="font-normal text-muted">· {w.species}</span>
                  </span>
                  <RiskBadge risk={w.risk} prefix="" />
                </div>
                <div className="mono mt-1 text-xs text-ink2">
                  Rain {w.rain_pct_normal != null ? `${w.rain_pct_normal.toFixed(0)}%` : "—"} of normal (90d)
                  {` · ${w.hot_days_30d} hot days (30d)`}
                  {w.frost_forecast_days ? ` · frost forecast ${w.frost_forecast_days}d` : ""}
                </div>
                {w.risk !== "Low" && <div className="mt-0.5 text-xs text-muted">{w.reasons.join(" · ")}</div>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <Lots lots={d.lots} shrinkage={d.shrinkage} name={d.name} iso={d.iso_code} />
    </>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div className="mono mt-1 font-semibold">{value}</div>
    </div>
  );
}

function HarvestStrip({ main, fly }: { main: number[]; fly: number[] }) {
  const now = new Date().getMonth() + 1;
  return (
    <div className="mt-2">
      <div className="grid grid-cols-12 gap-1">
        {MONTHS.map((l, i) => {
          const m = i + 1;
          const bg = main.includes(m) ? "bg-[var(--accent)]" : fly.includes(m) ? "bg-[var(--accent-ring)]" : "bg-surface2";
          return (
            <div key={m} className="text-center">
              <div className={`h-2 rounded-sm ${bg} ${m === now ? "ring-2 ring-[var(--text-primary)] ring-offset-1 ring-offset-[var(--surface-1)]" : ""}`} />
              <div className={`mt-1 text-[10px] ${m === now ? "font-semibold text-ink" : "text-muted"}`}>{l}</div>
            </div>
          );
        })}
      </div>
      <div className="mt-1.5 flex gap-3 text-[11px] text-muted">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-2 w-3 rounded-sm bg-[var(--accent)]" /> Main crop
        </span>
        {fly.length > 0 && (
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-3 rounded-sm bg-[var(--accent-ring)]" /> Fly / mitaca
          </span>
        )}
      </div>
    </div>
  );
}

function SpeciesBar({ share }: { share: Record<string, number> }) {
  const entries = Object.entries(share);
  if (!entries.length) return <div className="mt-2 text-sm text-muted">—</div>;
  const color: Record<string, string> = { Arabica: "var(--series-1)", Robusta: "var(--series-4)", Liberica: "var(--series-3)" };
  return (
    <div className="mt-2">
      <div className="flex h-2 gap-0.5 overflow-hidden rounded-full">
        {entries.map(([k, v]) => (
          <div key={k} style={{ width: `${v}%`, background: color[k] ?? "var(--axis)" }} />
        ))}
      </div>
      <div className="mt-1.5 flex gap-3 text-xs text-ink2">
        {entries.map(([k, v]) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: color[k] ?? "var(--axis)" }} />
            {k} <span className="mono text-muted">{v}%</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function Lots({ lots, shrinkage, name, iso }: { lots: Lot[]; shrinkage: number; name: string; iso: string }) {
  const [sort, setSort] = useState<Sort>("landed");
  const m = useMoney();
  const sorted = useMemo(
    () =>
      [...lots].sort((a, b) =>
        sort === "landed" ? (a.landed_usd_lb ?? 1e9) - (b.landed_usd_lb ?? 1e9) : (b.sca_score ?? 0) - (a.sca_score ?? 0),
      ),
    [lots, sort],
  );
  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-2">
        <div>
          <div className="card-title">Available lots</div>
          <div className="text-xs text-muted">
            {lots.length} lot{lots.length === 1 ? "" : "s"} · landed in Atlanta · roasted at {(shrinkage * 100).toFixed(0)}% shrink
          </div>
        </div>
        {lots.length > 1 && (
          <Segmented
            label="Sort lots"
            size="sm"
            value={sort}
            onChange={setSort}
            options={[
              { value: "landed", label: "Cheapest" },
              { value: "score", label: "Top score" },
            ]}
          />
        )}
      </div>
      {!lots.length ? (
        <Empty title={`No current offers for ${name}`}>Importer lists refresh daily; upload a spot sheet on Data Sources to add lots.</Empty>
      ) : (
        <ul className="space-y-2">
          {sorted.map((l) => (
            <li key={l.id} className="card card-hover !p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold" title={l.lot_name}>
                    {l.raw_url ? (
                      <a href={l.raw_url} target="_blank" rel="noreferrer" className="hover:underline">
                        {l.lot_name}
                      </a>
                    ) : (
                      l.lot_name
                    )}
                  </div>
                  <div className="mt-0.5 truncate text-xs text-ink2">
                    {[l.producer, l.importer].filter(Boolean).join(" · ")}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {l.species && <Badge>{l.species}</Badge>}
                    {l.process && <Badge>{l.process}</Badge>}
                    {l.variety && <Badge>{l.variety}</Badge>}
                    {l.sca_score != null && <Badge tone="accent">{l.sca_score.toFixed(1)} pts</Badge>}
                    {l.bag_weight_kg != null && <Badge>{l.bag_weight_kg.toFixed(0)} kg bag</Badge>}
                    {l.bags_available != null && <Badge>{l.bags_available} avail.</Badge>}
                    {l.warehouse && <Badge tone={l.is_southeast ? "pos" : "neutral"}>{l.warehouse}</Badge>}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="eyebrow">Landed ATL</div>
                  <Price usdLb={l.landed_usd_lb} layout="stack" className="mt-1" />
                  <div className="mono mt-1.5 text-[11px] text-muted">
                    spot {m.fmt(l.spot_usd_lb)}
                    {m.suffix} · roasted {m.fmt(l.roasted_usd_lb)}
                    {m.suffix}
                  </div>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex gap-3 text-sm">
        <Link href={`/trends?country=${iso}`} className="link">
          5-year trend →
        </Link>
        <Link href={`/offers?country=${iso}`} className="link">
          All offers →
        </Link>
      </div>
    </section>
  );
}
