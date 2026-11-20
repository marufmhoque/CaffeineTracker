"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json", ...init?.headers } : init?.headers,
    cache: "no-store",
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

/** Fetch hook that keeps the previous data while refetching (no skeleton flash). */
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (!path) return;
    const id = ++seq.current;
    setLoading(true);
    try {
      const d = await api<T>(path);
      if (id === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (id === seq.current) setError((e as Error).message);
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    load();
  }, [load]);

  return { data, error, loading, reload: load };
}

// ---- API types

export type Country = {
  iso_code: string;
  m49: number;
  name: string;
  region: string;
  species_mix: string;
  currency_code: string;
  ico_group: string;
  spot_avg_usd_lb: number | null;
  spot_offer_count: number;
  spot_basis: "bags" | "small packs" | null;
  southeast_offer_count: number;
  diff_vs_c_usd_lb: number | null;
  diff_basis: "spot" | "trade" | null;
  typical_diff_usd_lb: [number | null, number | null];
  trade_unit_usd_lb: number | null;
  trade_period: string | null;
  trade_freq: "M" | "A" | null;
  trade_reporters: string[];
  chg_7d_pct: number | null;
  chg_30d_pct: number | null;
  chg_yoy_pct: number | null;
  change_basis: "spot" | "trade" | null;
  harvest_status: string;
  main_harvest_months: number[];
  fly_harvest_months: number[];
};

export type Matrix = { as_of: string; ice_arabica_usd_lb: number | null; countries: Country[] };

export type FxRow = {
  currency: string;
  date: string;
  per_usd: number;
  chg_7d_pct: number | null;
  chg_30d_pct: number | null;
  chg_1y_pct: number | null;
};

export type Summary = {
  ice_arabica: {
    date: string | null;
    usd_lb: number | null;
    ma30_usd_lb: number | null;
    high_52w: number | null;
    low_52w: number | null;
    chg_1d_pct: number | null;
    chg_30d_pct: number | null;
    chg_1y_pct: number | null;
  };
  ice_robusta: { date: string; usd_lb: number } | null;
  ico: { date: string; composite_usd_lb: number; groups: Record<string, number> } | null;
  fx: FxRow[];
};

export type Offer = {
  id: number;
  importer: string;
  source: string;
  scraped_at: string;
  offer_date: string | null;
  country_iso: string | null;
  country: string | null;
  lot_name: string;
  origin_region: string | null;
  process: string | null;
  variety: string | null;
  sca_score: number | null;
  bag_weight_kg: number | null;
  warehouse_location: string | null;
  warehouse_tier: string | null;
  is_southeast: boolean;
  spot_usd_lb: number;
  freight_usd_lb: number | null;
  landed_usd_lb: number | null;
  roasted_usd_lb: number | null;
  bags_available: number | null;
  raw_url: string | null;
};

export type Alert = {
  id: number;
  created_at: string;
  country_iso: string | null;
  alert_type: string;
  message: string;
  is_read: boolean;
  data_json: Record<string, unknown>;
};

export type HistoryPoint = {
  date: string;
  ice_c_usd_lb: number | null;
  spot_avg_usd_lb: number | null;
  spot_n: number;
  diff_usd_lb: number | null;
};

export type TradePoint = {
  period: string;
  freq: "M" | "A";
  reporter: string;
  unit_usd_lb: number;
  volume_kg: number;
  source: string;
};

export type History = {
  country: string;
  process: string | null;
  range: string;
  points: HistoryPoint[];
  trade: TradePoint[];
  spread_trend: { usd_lb_per_30d: number; direction: string } | null;
  processes: string[];
};

export type FreightRules = {
  destination: string;
  warehouse_fee_usd_lb: number;
  default_shrinkage: number;
  unknown_warehouse_freight_usd_lb: number;
  tiers: {
    id: string;
    label: string;
    low: number;
    high: number;
    default: number;
    southeast?: boolean;
    warehouses: { name: string; aliases: string[] }[];
  }[];
};
