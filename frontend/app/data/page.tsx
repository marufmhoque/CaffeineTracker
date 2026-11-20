"use client";

import { useRef, useState } from "react";
import { ErrorNote, PageHeader, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/api";

type Run = { id: number; job: string; started_at: string; finished_at: string | null; status: string; rows: number; message: string };
type FileRow = { id: number; filename: string; ingested_at: string; importer: string | null; rows: number; status: string; message: string };
type Sources = {
  scrapers: { key: string; importer: string; base_url: string; default_warehouse: string | null }[];
  enabled: string;
  portals: { importer: string; login_url: string }[];
  dropzone: string;
  comtrade_key: boolean;
  census_key: boolean;
  llm_extraction: boolean;
  email_webhook: boolean;
};

const STATUS: Record<string, string> = {
  ok: "text-pos",
  partial: "text-warn",
  error: "text-neg",
  running: "text-accent-strong",
  duplicate: "text-muted",
};

const JOBS = [
  { job: "benchmarks", label: "Tier 1 · ICE + FX", desc: "Daily 6:15pm ET (weekdays)" },
  { job: "market", label: "ICE forward curve + certified stocks", desc: "Daily 6:25pm ET (weekdays)" },
  { job: "weather", label: "Origin weather anomalies (Open-Meteo)", desc: "Daily 5:30am" },
  { job: "usda", label: "USDA FAS crop balance sheets", desc: "Weekly, Sunday 5:30am" },
  { job: "trade", label: "Tier 3 · Customs unit values", desc: "Weekly, Sunday 5am" },
  { job: "scrapers", label: "Tier 2 · Importer scrapers", desc: "Daily 7am" },
  { job: "portals", label: "Tier 2 · Authenticated portals", desc: "Weekdays 6:30am (Playwright)" },
  { job: "dropzone", label: "Tier 2 · Spot-sheet folder", desc: "Every 5 minutes" },
  { job: "alerts", label: "Alert engine + digest", desc: "Daily 7:45am" },
];

export default function DataSources() {
  const sources = useApi<Sources>("/ingest/sources");
  const runs = useApi<{ runs: Run[]; files: FileRow[] }>("/ingest/runs?limit=60");
  const [busy, setBusy] = useState<string | null>(null);
  const [upload, setUpload] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const matches = (r: Run, job: string) => r.job === job || (job === "scrapers" && r.job.startsWith("scrape:"));

  const run = async (job: string) => {
    setBusy(job);
    const started = Date.now();
    try {
      await api(`/ingest/run/${job}`, { method: "POST" });
      // Jobs run in the background (the trade backfill can take minutes); poll the run log.
      for (let i = 0; i < 200; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const d = await api<{ runs: Run[] }>("/ingest/runs?limit=20");
        const mine = d.runs.filter((r) => matches(r, job) && new Date(r.started_at).getTime() >= started - 5000);
        if (mine.length && mine.every((r) => r.status !== "running")) break;
        if (!mine.length && i > 3 && job === "dropzone") break; // nothing to ingest
      }
    } catch (e) {
      setUpload(`${job}: ${(e as Error).message}`);
    } finally {
      setBusy(null);
      runs.reload();
    }
  };

  const send = async (files: FileList | null) => {
    if (!files?.length) return;
    const out: string[] = [];
    for (const f of Array.from(files)) {
      const fd = new FormData();
      fd.append("file", f);
      try {
        const r = await api<{ status: string; rows?: number; importer?: string; message?: string }>("/ingest/upload", { method: "POST", body: fd });
        out.push(r.status === "ok" ? `✓ ${f.name}: ${r.rows} offers (${r.importer ?? "importer from file"})` : `✗ ${f.name}: ${r.message ?? r.status}`);
      } catch (e) {
        out.push(`✗ ${f.name}: ${(e as Error).message}`);
      }
    }
    setUpload(out.join("\n"));
    runs.reload();
  };

  const sendMarket = async (kind: "robusta" | "certified_stocks", files: FileList | null) => {
    const f = files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await api<{ rows: number }>(`/ingest/market-csv?kind=${kind}`, { method: "POST", body: fd });
      setUpload(`✓ ${f.name}: ${r.rows} ${kind === "robusta" ? "Robusta settlements" : "certified-stock rows"} loaded`);
    } catch (e) {
      setUpload(`✗ ${f.name}: ${(e as Error).message}`);
    }
    runs.reload();
  };

  const s = sources.data;
  return (
    <>
      <PageHeader title="Data sources" subtitle="Three ingestion tiers: market benchmarks, importer spot offers, and customs trade valuations for every producing country." />
      <ErrorNote error={sources.error} />

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card">
          <h2 className="card-title mb-2">Pipelines</h2>
          <ul className="divide-y divide-[var(--border)]">
            {JOBS.map((j) => {
              const last = runs.data?.runs.find((r) => matches(r, j.job));
              return (
                <li key={j.job} className="flex items-center justify-between gap-3 py-2">
                  <div>
                    <div className="text-sm font-medium">{j.label}</div>
                    <div className="text-xs text-muted">
                      {j.desc}
                      {last && (
                        <>
                          {" · last "}
                          <span className={STATUS[last.status]}>{last.status}</span> {timeAgo(last.started_at)}
                        </>
                      )}
                    </div>
                  </div>
                  <button className="btn" disabled={busy !== null} onClick={() => run(j.job)}>
                    {busy === j.job ? "Running…" : "Run now"}
                  </button>
                </li>
              );
            })}
          </ul>
          {s && (
            <ul className="mt-3 space-y-0.5 border-t border-border pt-3 text-xs text-ink2">
              <li>UN Comtrade: {s.comtrade_key ? "API key set (full data API)" : "public preview endpoint (no key)"}</li>
              <li>US Census monthly imports: {s.census_key ? "enabled" : "set CENSUS_API_KEY to enable real US monthly data"}</li>
              <li>PDF fallback extraction with Claude: {s.llm_extraction ? "enabled" : "set ANTHROPIC_API_KEY to enable"}</li>
              <li>Email-forward webhook: {s.email_webhook ? "POST /api/ingest/email?token=…" : "set INGEST_WEBHOOK_TOKEN to enable"}</li>
            </ul>
          )}
        </section>

        <section className="card">
          <h2 className="card-title mb-2">Drop a spot sheet</h2>
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              send(e.dataTransfer.files);
            }}
            onClick={() => fileRef.current?.click()}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && fileRef.current?.click()}
            role="button"
            tabIndex={0}
            className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-10 text-center ${drag ? "border-[var(--accent)] bg-accent-soft" : "border-[var(--border-strong)]"}`}
          >
            <div className="font-medium">Drop CSV, XLSX or PDF spot sheets here</div>
            <div className="mt-1 text-sm text-ink2">or click to choose files — Cafe Imports, Atlas, Ally, Olam/ofi, Sucafina…</div>
            <input ref={fileRef} type="file" multiple accept=".csv,.tsv,.xlsx,.xls,.xlsm,.pdf" className="hidden" onChange={(e) => send(e.target.files)} />
          </div>
          {upload && <pre className="mt-2 whitespace-pre-wrap text-sm">{upload}</pre>}
          {s && (
            <p className="mt-2 text-xs text-muted">
              Files saved to <code>{s.dropzone}</code> are picked up automatically. Name files like <code>atlas_2026-11-14.pdf</code> so the importer and offer date are detected.
            </p>
          )}
          <h3 className="card-title mb-1 mt-5">Exchange data without a free feed</h3>
          <p className="mb-2 text-xs text-muted">
            ICE Robusta settlements and ICE certified stocks aren&apos;t available from a free API. Upload the CSVs here; files are kept so a rebuilt database reloads them.
          </p>
          <div className="flex flex-wrap gap-2">
            <MarketUpload label="ICE Robusta settlements" hint="date,usd_tonne" onFile={(f) => sendMarket("robusta", f)} />
            <MarketUpload label="ICE certified stocks" hint="date,bags" onFile={(f) => sendMarket("certified_stocks", f)} />
          </div>
          <h3 className="card-title mb-1 mt-5">Public catalog scrapers</h3>
          <ul className="text-sm text-ink2">
            {s?.scrapers.map((x) => (
              <li key={x.key}>
                {x.importer} <span className="text-muted">· {x.default_warehouse ?? "warehouse from listing"}</span>
              </li>
            ))}
            {s?.portals.map((p) => (
              <li key={p.importer}>
                {p.importer} <span className="text-muted">· authenticated portal</span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="card mt-4 overflow-x-auto p-0">
        <h2 className="card-title px-5 pt-5">Recent runs</h2>
        <table className="mt-2 w-full">
          <thead>
            <tr>
              <th className="th">Job</th>
              <th className="th">Started</th>
              <th className="th">Status</th>
              <th className="th num">Rows</th>
              <th className="th">Notes</th>
            </tr>
          </thead>
          <tbody>
            {runs.data?.runs.map((r) => (
              <tr key={r.id} className="border-t border-border align-top">
                <td className="td font-medium">{r.job}</td>
                <td className="td text-ink2">{new Date(r.started_at).toLocaleString()}</td>
                <td className={`td font-medium ${STATUS[r.status] ?? ""}`}>{r.status}</td>
                <td className="td num">{r.rows}</td>
                <td className="td max-w-xl whitespace-normal text-xs text-ink2">{r.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!!runs.data?.files.length && (
          <>
            <h2 className="card-title px-5 pt-5">Ingested spot sheets</h2>
            <table className="mt-2 w-full">
              <thead>
                <tr>
                  <th className="th">File</th>
                  <th className="th">Importer</th>
                  <th className="th">Status</th>
                  <th className="th num">Offers</th>
                  <th className="th">Notes</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.files.map((f) => (
                  <tr key={f.id} className="border-t border-border align-top">
                    <td className="td">{f.filename}</td>
                    <td className="td text-ink2">{f.importer ?? "—"}</td>
                    <td className={`td font-medium ${STATUS[f.status] ?? ""}`}>{f.status}</td>
                    <td className="td num">{f.rows}</td>
                    <td className="td max-w-xl whitespace-normal text-xs text-ink2">{f.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>
    </>
  );
}

function MarketUpload({ label, hint, onFile }: { label: string; hint: string; onFile: (f: FileList | null) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <button className="btn" onClick={() => ref.current?.click()}>
        Upload {label} <span className="mono text-[11px] font-normal text-muted">{hint}</span>
      </button>
      <input
        ref={ref}
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        onChange={(e) => {
          onFile(e.target.files);
          e.target.value = "";
        }}
      />
    </>
  );
}
