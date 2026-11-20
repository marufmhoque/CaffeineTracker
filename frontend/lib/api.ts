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

export type Matrix = {
  as_of: string;
  species: "All" | "Arabica" | "Robusta";
  benchmark: "arabica" | "robusta";
  benchmark_usd_lb: number | null;
  ice_arabica_usd_lb: number | null;
  countries: Country[];
};

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
  ice_robusta: {
    date: string;
    usd_lb: number;
    chg_1d_pct: number | null;
    chg_30d_pct: number | null;
    chg_1y_pct: number | null;
    high_52w: number | null;
    low_52w: number | null;
  } | null;
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
  species: string | null;
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
  ice_robusta_usd_lb?: number | null;
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
  tariff_pct?: number;
  ocean_surcharge_usd_lb?: number;
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

// ---- 5-year ranges, signals, origin drawer

export type BenchRange = "1m" | "6m" | "1y" | "3y" | "5y" | "max";

export type BenchHistory = {
  range: BenchRange;
  interval: "daily" | "weekly";
  points: { date: string; arabica: number | null; robusta: number | null }[];
  arabica: { first: number; last: number; high: number; low: number; chg_pct: number | null } | null;
  robusta: { first: number; last: number; high: number; low: number; chg_pct: number | null } | null;
  earliest: string | null;
};

export type CurveContract = { symbol: string; contract_month: string; label: string; usd_lb: number };

export type Curve = {
  date: string | null;
  contracts: CurveContract[];
  structure: { state: "Contango" | "Backwardation" | "Flat"; slope_pct: number; annualized_pct: number; months: number; takeaway: string } | null;
  month_ago_date?: string | null;
  month_ago: CurveContract[];
};

export type WeatherBelt = {
  as_of: string;
  country_iso: string;
  belt: string;
  species: string;
  lat: number;
  lon: number;
  rain_90d_mm: number | null;
  rain_normal_mm: number | null;
  rain_pct_normal: number | null;
  hot_days_30d: number;
  frost_days_30d: number;
  frost_forecast_days: number;
  hot_forecast_days: number;
  risk: "Low" | "Moderate" | "Critical";
  reasons: string[];
};

export type CropCycle = {
  market_year: string;
  production_k_bags: number;
  arabica_k_bags: number | null;
  robusta_k_bags: number | null;
  yoy_pct: number | null;
  vs_5y_avg_pct: number | null;
  cycle: "On-year" | "Off-year" | null;
  cycle_basis: string;
};

export type Signals = {
  curve: Curve;
  weather: WeatherBelt[];
  certified_stocks: {
    latest: { date: string; bags: number } | null;
    range_5y?: { low: number; high: number; position_pct: number };
    chg_90d_pct?: number | null;
    pinch: boolean | null;
    takeaway?: string;
    series: { date: string; bags: number; ice_usd_lb: number | null }[];
  };
  crops: {
    world: {
      market_year: string;
      production_k_bags: number;
      consumption_k_bags: number;
      surplus_k_bags: number;
      surplus_pct: number | null;
      ending_stocks_k_bags: number | null;
      arabica_yoy_pct?: number;
      robusta_yoy_pct?: number;
      history: { year: number; production: number; consumption: number }[];
    } | null;
    origins: (CropCycle & { iso_code: string; name: string })[];
  };
};

export type Lot = {
  id: number;
  importer: string;
  lot_name: string;
  producer: string | null;
  process: string | null;
  variety: string | null;
  species: string | null;
  sca_score: number | null;
  bag_weight_kg: number | null;
  bags_available: number | null;
  warehouse: string | null;
  is_southeast: boolean;
  spot_usd_lb: number;
  landed_usd_lb: number | null;
  roasted_usd_lb: number | null;
  raw_url: string | null;
};

export type CountryDetail = {
  iso_code: string;
  name: string;
  region: string;
  species_mix: string;
  species_share: Record<string, number>;
  species_share_basis: string;
  currency_code: string;
  ico_group: string;
  harvest_status: string;
  main_harvest_months: number[];
  fly_harvest_months: number[];
  default_bag_kg: number;
  typical_diff_usd_lb: [number | null, number | null];
  prices: Country | null;
  weather_risk: "Low" | "Moderate" | "Critical" | null;
  weather: WeatherBelt[];
  crop: CropCycle | null;
  shrinkage: number;
  lots: Lot[];
};
