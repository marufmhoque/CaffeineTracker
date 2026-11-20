"use client";

import Link from "next/link";
import { useState } from "react";
import { Empty, ErrorNote, PageHeader, timeAgo } from "@/components/ui";
import { api, useApi, type Alert } from "@/lib/api";

const LABELS: Record<string, { label: string; icon: string }> = {
  origin_price_drop: { label: "Origin price drop", icon: "▼" },
  value_per_point: { label: "Value-per-point deal", icon: "★" },
  southeast_arrival: { label: "Southeast arrival", icon: "⚓" },
  fx_weakening: { label: "FX trigger", icon: "¤" },
};

export default function Alerts() {
  const [type, setType] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const qs = new URLSearchParams({ limit: "300" });
  if (type) qs.set("alert_type", type);
  if (unreadOnly) qs.set("unread", "true");
  const { data, error, reload } = useApi<{ unread: number; types: string[]; alerts: Alert[] }>(`/alerts?${qs}`);
  const [running, setRunning] = useState(false);

  const markRead = async (id: number) => {
    await api(`/alerts/${id}/read`, { method: "POST" });
    reload();
  };

  return (
    <>
      <PageHeader title="Deal alerts" subtitle="Origin price drops (>5% / 14 days), 87+ lots under their 30-day average, new Savannah/Charleston/Jacksonville arrivals, and producer-currency moves (>3% / week).">
        <button
          className="btn"
          disabled={running}
          onClick={async () => {
            setRunning(true);
            try {
              await api("/ingest/run/alerts?wait=true", { method: "POST" });
            } finally {
              setRunning(false);
              reload();
            }
          }}
        >
          {running ? "Checking…" : "Check now"}
        </button>
        <button
          className="btn"
          onClick={async () => {
            await api("/alerts/read-all", { method: "POST" });
            reload();
          }}
        >
          Mark all read
        </button>
      </PageHeader>
      <ErrorNote error={error} />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select className="input" value={type} onChange={(e) => setType(e.target.value)} aria-label="Alert type">
          <option value="">All types</option>
          {Object.entries(LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v.label}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-sm text-ink2">
          <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} /> Unread only
        </label>
        <span className="ml-auto text-sm text-muted">{data?.unread ?? 0} unread</span>
      </div>

      {data?.alerts.length ? (
        <ul className="card divide-y divide-[var(--border)] p-0">
          {data.alerts.map((a) => {
            const meta = LABELS[a.alert_type] ?? { label: a.alert_type, icon: "•" };
            return (
              <li key={a.id} className={`flex gap-3 px-4 py-3 ${a.is_read ? "opacity-70" : ""}`}>
                <span aria-hidden className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface2 text-sm">
                  {meta.icon}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-xs text-muted">
                    <span className="font-medium text-ink2">{meta.label}</span> · {timeAgo(a.created_at)}
                    {a.country_iso && (
                      <>
                        {" · "}
                        <Link className="hover:underline" href={`/trends?country=${a.country_iso}`}>
                          {a.country_iso} trend
                        </Link>
                      </>
                    )}
                  </div>
                  <div className={`mt-0.5 text-sm ${a.is_read ? "" : "font-medium"}`}>{a.message}</div>
                </div>
                {!a.is_read && (
                  <button className="self-start text-xs text-accent-strong hover:underline" onClick={() => markRead(a.id)}>
                    Mark read
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <Empty title="No alerts">The engine runs daily at 7:45 ET after the scrapers; use “Check now” to run it immediately.</Empty>
      )}
      <p className="mt-2 text-xs text-muted">
        Set <code>SLACK_WEBHOOK_URL</code> or <code>SMTP_*</code> in <code>.env</code> to receive the daily digest. Terminal: <code>python -m backend.app.cli digest</code>.
      </p>
    </>
  );
}
