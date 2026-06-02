/**
 * AUDIT → Air Watch
 *
 * Historique des détections RF émises par le scanner (page Réseau →
 * Radio). Liste filtrable + ack/restore pour curer les faux positifs.
 * Le score AUDIT global incorporera bientôt les active alerts.
 */

import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Radio,
  RotateCcw,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  dismissThreat,
  getAirWatchSummary,
  restoreThreat,
} from "@/api/wifi-radio";
import { errorMessage } from "@/lib/error-utils";
import { cn } from "@/lib/utils";
import type { ThreatEventDb, ThreatLevel } from "@/types/wifi-radio";

const LEVEL_STYLE: Record<ThreatLevel, string> = {
  alert: "text-red-300 border-red-500/40 bg-red-500/5",
  warn: "text-amber-300 border-amber-500/40 bg-amber-500/5",
  info: "text-cyan-300 border-cyan-500/30 bg-cyan-500/5",
};

const KIND_LABEL: Record<string, string> = {
  evil_twin: "Evil twin",
  legacy_crypto: "Crypto déprécié",
  wps_enabled: "WPS actif",
  strong_neighbor: "Voisin co-canal fort",
};

export default function AirWatch() {
  const qc = useQueryClient();
  const summary = useQuery({
    queryKey: ["air-watch"],
    queryFn: getAirWatchSummary,
    refetchOnWindowFocus: false,
  });

  const dismissMut = useMutation({
    mutationFn: (id: number) => dismissThreat(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["air-watch"] }),
  });
  const restoreMut = useMutation({
    mutationFn: (id: number) => restoreThreat(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["air-watch"] }),
  });

  const events = summary.data?.events ?? [];
  const active = useMemo(
    () => events.filter((e) => !e.dismissed),
    [events],
  );
  const dismissed = useMemo(
    () => events.filter((e) => e.dismissed),
    [events],
  );

  return (
    <div className="space-y-6">
      <header>
        <h1 className="cyber-display cyber-glow text-2xl">
          AIR WATCH · RF DETECTIONS
        </h1>
        <p className="cyber-label text-[10px] mt-1">
          Détections du scanner WiFi (evil twin · WPS · crypto déprécié ·
          voisins forts). Lancer un scan depuis{" "}
          <Link
            to="/networks/radio"
            className="text-[color:var(--color-cyber-accent)] underline"
          >
            Réseau → Radio
          </Link>{" "}
          alimente cette page.
        </p>
      </header>

      {summary.isError && (
        <div className="cyber-chip cyber-chip-on px-3 py-2 text-xs">
          {errorMessage(summary.error)}
        </div>
      )}

      {summary.data && (
        <>
          <StatsCards data={summary.data} />

          <section>
            <header className="cyber-label text-[10px] mb-2 flex items-center gap-2">
              <AlertTriangle className="h-3 w-3" /> menaces actives (
              {active.length})
            </header>
            {active.length === 0 ? (
              <p className="text-xs text-[color:var(--color-cyber-muted)]">
                // aucune menace active. Lance un scan depuis la page Radio.
              </p>
            ) : (
              <ThreatList
                events={active}
                onDismiss={(id) => dismissMut.mutate(id)}
                disabled={dismissMut.isPending || restoreMut.isPending}
              />
            )}
          </section>

          {dismissed.length > 0 && (
            <section>
              <header className="cyber-label text-[10px] mb-2 flex items-center gap-2">
                <Check className="h-3 w-3" /> menaces dismissed (
                {dismissed.length})
              </header>
              <ThreatList
                events={dismissed}
                onRestore={(id) => restoreMut.mutate(id)}
                disabled={dismissMut.isPending || restoreMut.isPending}
                dimmed
              />
            </section>
          )}
        </>
      )}
    </div>
  );
}

function StatsCards({
  data,
}: {
  data: {
    total: number;
    active: number;
    dismissed: number;
    by_level: Record<string, number>;
    by_kind: Record<string, number>;
  };
}) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <StatCard label="Total" value={data.total} accent="cyber" />
      <StatCard label="Actives" value={data.active} accent="warn" />
      <StatCard
        label="Alerts"
        value={data.by_level["alert"] ?? 0}
        accent="alert"
      />
      <StatCard label="Dismissed" value={data.dismissed} accent="dim" />
    </div>
  );
}

function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent: "cyber" | "warn" | "alert" | "dim";
}) {
  const color = {
    cyber: "text-[color:var(--color-cyber-accent)]",
    warn: "text-amber-300",
    alert: "text-red-300",
    dim: "text-[color:var(--color-cyber-muted)]",
  }[accent];
  return (
    <div className="cyber-panel p-3">
      <div className="cyber-label text-[9px]">{label}</div>
      <div className={cn("cyber-glow font-mono text-2xl", color)}>{value}</div>
    </div>
  );
}

function ThreatList({
  events,
  onDismiss,
  onRestore,
  disabled,
  dimmed,
}: {
  events: ThreatEventDb[];
  onDismiss?: (id: number) => void;
  onRestore?: (id: number) => void;
  disabled: boolean;
  dimmed?: boolean;
}) {
  return (
    <div className="space-y-2">
      {events.map((e) => (
        <div
          key={e.id}
          className={cn(
            "cyber-panel border p-3 flex items-start gap-3",
            LEVEL_STYLE[e.level],
            dimmed && "opacity-50",
          )}
        >
          <Radio className="h-4 w-4 flex-shrink-0 mt-0.5" />
          <div className="flex-1 space-y-1">
            <div className="text-xs font-mono">
              <span className="cyber-label">[{KIND_LABEL[e.kind] ?? e.kind}]</span>{" "}
              {e.message}
            </div>
            <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
              <span className="font-mono">{e.bssid}</span> · ssid « {e.ssid || "—"} »
              · ch {e.channel} · {e.rssi_dbm} dBm
            </div>
            <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
              première détection :{" "}
              {new Date(e.first_seen_at).toLocaleString("fr-FR")} · dernière :{" "}
              {new Date(e.last_seen_at).toLocaleString("fr-FR")}
            </div>
          </div>
          <div className="flex flex-col gap-1">
            {!e.dismissed && onDismiss && (
              <button
                onClick={() => onDismiss(e.id)}
                disabled={disabled}
                className="cyber-button-ghost px-2 py-1 text-[10px]"
                title="Marquer comme faux positif / risque accepté"
              >
                <Check className="inline h-3 w-3 mr-1" />
                dismiss
              </button>
            )}
            {e.dismissed && onRestore && (
              <button
                onClick={() => onRestore(e.id)}
                disabled={disabled}
                className="cyber-button-ghost px-2 py-1 text-[10px]"
              >
                <RotateCcw className="inline h-3 w-3 mr-1" />
                restore
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
