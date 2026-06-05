// Surveillance session detail with classified timeline (Q2-C).
//
// Top: session header + countdown / completion stats.
// Body: per-BSSID timeline rows. Each row = SSID/vendor/classification
// badge + a heatmap-style bar where each cell is a time bucket coloured
// by RSSI (white-to-red gradient). Empty bucket = AP not seen in that
// window. Filter chips above (stable/edge/drifting/transient).

import { useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, MapPin, RadioTower, RefreshCw } from "lucide-react";

import VendorLogo from "@/components/VendorLogo";

import {
  type Classification,
  type TimelineRow,
  CLASSIFICATION_META,
  getSurveillanceSession,
  getSurveillanceTimeline,
} from "@/api/surveillance";
import { cn } from "@/lib/utils";

const FILTERS: ReadonlyArray<{
  value: Classification | "all";
  label: string;
}> = [
  { value: "all", label: "tous" },
  { value: "stable", label: "stable" },
  { value: "edge", label: "edge" },
  { value: "drifting", label: "drifting" },
  { value: "transient", label: "transient" },
];

export default function SurveillanceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const sessionId = Number(id);

  const session = useQuery({
    queryKey: ["wifi", "surveillance", sessionId],
    queryFn: () => getSurveillanceSession(sessionId),
    refetchInterval: 10_000,
    enabled: Number.isFinite(sessionId),
  });
  const timeline = useQuery({
    queryKey: ["wifi", "surveillance", sessionId, "timeline"],
    queryFn: () => getSurveillanceTimeline(sessionId, 100),
    refetchInterval: 15_000,
    enabled: Number.isFinite(sessionId),
  });

  const [filter, setFilter] = useState<Classification | "all">("all");
  const [hideRandomized, setHideRandomized] = useState(false);

  // All hooks must be called unconditionally on every render (Rules of
  // Hooks) — keep the useMemos here, BEFORE any early return.
  const filteredRows = useMemo(() => {
    if (!timeline.data) return [];
    return timeline.data.rows.filter((r) => {
      if (hideRandomized && r.is_randomized) return false;
      if (filter === "all") return true;
      return r.classification === filter;
    });
  }, [timeline.data, filter, hideRandomized]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {
      all: timeline.data?.rows.length ?? 0,
      stable: 0,
      edge: 0,
      drifting: 0,
      transient: 0,
    };
    for (const r of timeline.data?.rows ?? []) {
      c[r.classification] = (c[r.classification] ?? 0) + 1;
    }
    return c;
  }, [timeline.data]);

  if (!session.data) {
    return (
      <div className="cyber-card p-4 text-xs text-[color:var(--color-cyber-muted)]">
        Chargement de la session…
      </div>
    );
  }

  const s = session.data;
  const startedAt = new Date(s.started_at);
  const endedAt = s.ended_at ? new Date(s.ended_at) : null;
  const elapsedS =
    ((endedAt ? endedAt.getTime() : Date.now()) - startedAt.getTime()) / 1000;
  const progress = Math.min(
    100,
    Math.round((elapsedS / s.target_duration_s) * 100),
  );

  const tlData = timeline.data;

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between gap-2">
        <Link
          to="/networks/surveillance"
          className="cyber-button-ghost px-2 py-1 text-[10px] flex items-center gap-1"
        >
          <ArrowLeft className="h-3 w-3" /> retour
        </Link>
        <div className="flex items-center gap-2">
          <StatusChip status={s.status} />
          <button
            onClick={() => timeline.refetch()}
            disabled={timeline.isFetching}
            className="cyber-button-ghost p-1 text-xs"
            title="Rafraîchir la timeline"
          >
            <RefreshCw
              className={cn(
                "h-3 w-3",
                timeline.isFetching && "animate-spin",
              )}
            />
          </button>
        </div>
      </header>

      <section className="cyber-card p-4">
        <header className="cyber-label text-[10px] mb-2 flex items-center gap-2">
          <RadioTower className="h-3 w-3" /> session #{s.id} · {s.name}
        </header>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div>
            <div className="text-[9px] uppercase text-[color:var(--color-cyber-muted)]">
              débuté
            </div>
            <div className="font-mono">{startedAt.toLocaleString("fr-FR")}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-[color:var(--color-cyber-muted)]">
              durée
            </div>
            <div className="font-mono">
              {Math.floor(elapsedS / 60)} / {Math.floor(s.target_duration_s / 60)}{" "}
              min ({progress}%)
            </div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-[color:var(--color-cyber-muted)]">
              passes
            </div>
            <div className="font-mono">
              {s.total_passes}
              <span className="text-[color:var(--color-cyber-muted)] text-[10px]">
                {" "}
                / {s.interval_s}s interval
              </span>
            </div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-[color:var(--color-cyber-muted)]">
              BSSIDs uniques
            </div>
            <div className="font-mono">{s.unique_bssids}</div>
          </div>
        </div>
        {s.location_label && (
          <div className="mt-3 text-xs flex items-center gap-2 text-cyan-300">
            <MapPin className="h-3 w-3" />
            <span>{s.location_label}</span>
            {s.location_lat != null && s.location_lon != null && (
              <span className="text-[10px] font-mono text-[color:var(--color-cyber-muted)]">
                · {s.location_lat.toFixed(4)}, {s.location_lon.toFixed(4)}
              </span>
            )}
          </div>
        )}
        {s.note && (
          <div className="mt-3 text-[11px] text-[color:var(--color-cyber-muted)] border-l-2 border-[color:var(--color-cyber-border)] pl-2">
            {s.note}
          </div>
        )}
        {s.status === "active" && (
          <div className="mt-3 h-1 w-full bg-[color:var(--color-cyber-bg-2)] rounded-sm overflow-hidden">
            <div
              className="h-full bg-[color:var(--color-cyber-accent)] transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}
      </section>

      {tlData && tlData.rows.length === 0 ? (
        <div className="cyber-card p-4 text-xs text-[color:var(--color-cyber-muted)]">
          {s.total_passes === 0
            ? "Aucune passe terminée encore — patientez un cycle d'intervalle."
            : "Aucun BSSID détecté pendant les passes effectuées."}
        </div>
      ) : (
        <section className="cyber-card p-3">
          <header className="cyber-label text-[10px] mb-2 flex items-center justify-between gap-2 flex-wrap">
            <span>
              timeline · {filteredRows.length}/{tlData?.rows.length ?? 0} BSSIDs
            </span>
            <div className="flex items-center gap-1.5 flex-wrap">
              {FILTERS.map((f) => {
                const meta =
                  f.value === "all"
                    ? null
                    : CLASSIFICATION_META[f.value as Classification];
                const count = counts[f.value as string] ?? 0;
                return (
                  <button
                    key={f.value}
                    onClick={() => setFilter(f.value)}
                    className={cn(
                      "px-2 py-0.5 text-[10px] rounded-sm font-mono",
                      filter === f.value
                        ? "bg-[color:var(--color-cyber-accent)]/15 border border-[color:var(--color-cyber-accent)]"
                        : "border border-[color:var(--color-cyber-border)]/40",
                    )}
                    style={
                      meta
                        ? { color: meta.color }
                        : undefined
                    }
                    title={meta?.hint}
                  >
                    {meta?.icon} {f.label} ({count})
                  </button>
                );
              })}
              <label className="text-[10px] flex items-center gap-1 cursor-pointer ml-2">
                <input
                  type="checkbox"
                  checked={hideRandomized}
                  onChange={(e) => setHideRandomized(e.target.checked)}
                />
                masquer 🎭 random
              </label>
            </div>
          </header>

          <div className="space-y-1">
            {filteredRows.map((r) => (
              <TimelineRowView
                key={r.bssid}
                r={r}
                numBuckets={tlData?.buckets.length ?? 100}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function TimelineRowView({
  r,
  numBuckets,
}: {
  r: TimelineRow;
  numBuckets: number;
}) {
  const meta = CLASSIFICATION_META[r.classification];
  return (
    <div
      className="grid items-center gap-2 text-[11px] p-1.5 border-b border-[color:var(--color-cyber-border)]/30 hover:bg-[color:var(--color-cyber-bg-2)]/40"
      style={{
        gridTemplateColumns: "300px 80px 1fr 60px",
      }}
    >
      <div className="min-w-0 flex items-center gap-2">
        <VendorLogo
          slug={r.vendor_slug}
          vendor={r.vendor}
          isRandomized={r.is_randomized}
          size="md"
        />
        <div className="min-w-0 flex-1">
          <div className="font-mono truncate">
            {r.ssid || (
              <span className="italic text-[color:var(--color-cyber-muted)]">
                &lt;hidden&gt;
              </span>
            )}{" "}
            <span className="text-[color:var(--color-cyber-muted)] text-[10px]">
              · {r.bssid}
            </span>
          </div>
          <div className="text-[10px] text-[color:var(--color-cyber-muted)] truncate">
            ch {r.channel} · {r.band} GHz
            {!r.is_randomized && r.vendor && (
              <span className="ml-1.5 text-cyan-300">{r.vendor}</span>
            )}
          </div>
        </div>
      </div>
      <span
        className="font-mono text-[10px] px-1.5 py-0.5 rounded inline-flex items-center gap-1 w-fit"
        style={{
          color: meta.color,
          border: `1px solid ${meta.color}66`,
          background: `${meta.color}14`,
        }}
        title={meta.hint}
      >
        {meta.icon} {meta.label}
      </span>
      <TimelineHeatmap row={r} numBuckets={numBuckets} />
      <div className="text-[10px] text-right font-mono whitespace-nowrap">
        {Math.round(r.presence_ratio * 100)}%
        <div className="text-[9px] text-[color:var(--color-cyber-muted)]">
          {r.rssi_max}/{r.rssi_min} dBm
        </div>
      </div>
    </div>
  );
}

/** RSSI heat-bar : one cell per time bucket, intensity = RSSI. */
function TimelineHeatmap({
  row,
  numBuckets,
}: {
  row: TimelineRow;
  numBuckets: number;
}) {
  return (
    <div
      className="flex items-stretch h-5 rounded-sm overflow-hidden border border-[color:var(--color-cyber-border)]/40"
      title={`${row.passes_seen}/${numBuckets} buckets actifs · drift ${row.rssi_drift} dB`}
    >
      {row.buckets.map((b) => {
        if (b.rssi_dbm === null) {
          return (
            <div
              key={b.idx}
              className="flex-1 h-full"
              style={{ background: "transparent" }}
            />
          );
        }
        return (
          <div
            key={b.idx}
            className="flex-1 h-full"
            style={{ background: rssiToColor(b.rssi_dbm) }}
            title={`bucket #${b.idx} · ${b.rssi_dbm} dBm`}
          />
        );
      })}
    </div>
  );
}

/** Map RSSI [-100, -30] dBm → green-to-red heatmap colour. */
function rssiToColor(rssi: number): string {
  // Clamp to [-95, -35].
  const r = Math.max(-95, Math.min(-35, rssi));
  // 0.0 = weakest (-95), 1.0 = strongest (-35).
  const t = (r - -95) / (-35 - -95);
  // HSL : weak = blue (240), strong = red (0).
  const hue = 240 - t * 240;
  const sat = 70;
  const light = 50;
  return `hsl(${hue}, ${sat}%, ${light}%)`;
}

function StatusChip({ status }: { status: string }) {
  const map: Record<string, { label: string; color: string }> = {
    active: { label: "EN COURS", color: "var(--color-cyber-accent)" },
    completed: { label: "TERMINÉ", color: "#34d399" },
    cancelled: { label: "ANNULÉ", color: "#94a3b8" },
    failed: { label: "ÉCHEC", color: "#fbbf24" },
  };
  const m = map[status] ?? { label: status.toUpperCase(), color: "#94a3b8" };
  return (
    <span
      className="text-[10px] font-mono px-2 py-1 rounded-sm inline-flex items-center gap-1.5"
      style={{
        color: m.color,
        border: `1px solid ${m.color}88`,
        background: `${m.color}14`,
      }}
    >
      {status === "active" && (
        <span
          className="h-1.5 w-1.5 rounded-full animate-pulse"
          style={{ background: m.color }}
        />
      )}
      {m.label}
    </span>
  );
}
