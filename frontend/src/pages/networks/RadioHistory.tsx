/**
 * RÉSEAU → Radio · Historique
 *
 * Timeline of past scans for the active device. Filter by band, view
 * one scan's detail, delete entries. Each scan shows its geolocation
 * stamp if any.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Clock,
  History,
  MapPin,
  Radar,
  Trash2,
  Zap,
} from "lucide-react";
import {
  deleteScanHistory,
  getScanHistoryDetail,
  listScanHistory,
  type ScanHistoryRow,
} from "@/api/scan-history";
import { errorMessage } from "@/lib/error-utils";
import { useT } from "@/lib/i18n";
import {
  bucketColor,
  bucketFromRssi,
  bucketLabel,
  bucketRangeM,
} from "@/lib/rssi-distance";
import { cn } from "@/lib/utils";
import type { WifiBand } from "@/types/wifi";

export default function RadioHistory() {
  const t = useT();
  const qc = useQueryClient();
  const [bandFilter, setBandFilter] = useState<WifiBand | "all">("all");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const history = useQuery({
    queryKey: ["scan-history", bandFilter],
    queryFn: () =>
      listScanHistory(
        bandFilter === "all" ? { limit: 100 } : { band: bandFilter, limit: 100 },
      ),
  });

  const detail = useQuery({
    queryKey: ["scan-history", "detail", selectedId],
    queryFn: () => getScanHistoryDetail(selectedId!),
    enabled: selectedId != null,
  });

  const delMut = useMutation({
    mutationFn: (id: number) => deleteScanHistory(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scan-history"] });
      setSelectedId(null);
    },
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="cyber-display cyber-glow text-2xl">
          {t("net_radio_history.title").toUpperCase()}
        </h1>
        <p className="cyber-label text-[10px] mt-1">
          {t("net_radio_history.subtitle")}
        </p>
      </header>

      {/* Band filter */}
      <div className="flex gap-2">
        {(["all", "2", "5", "6"] as const).map((b) => (
          <button
            key={b}
            onClick={() => setBandFilter(b)}
            className={cn(
              "px-3 py-1 text-[10px] font-mono uppercase tracking-[0.15em] transition",
              bandFilter === b
                ? "border border-[color:var(--color-cyber-accent)] text-[color:var(--color-cyber-accent)]"
                : "border border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]",
            )}
          >
            {b === "all" ? "tous" : `${b} GHz`}
          </button>
        ))}
      </div>

      {history.isError && (
        <div className="cyber-chip cyber-chip-on px-3 py-2 text-xs">
          {errorMessage(history.error)}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {/* Left : timeline */}
        <section className="cyber-card p-3 space-y-2">
          <header className="cyber-label text-[10px] flex items-center gap-2">
            <History className="h-3 w-3" /> scans ({history.data?.length ?? 0})
          </header>
          {history.data && history.data.length === 0 && (
            <p className="text-xs text-[color:var(--color-cyber-muted)]">
              // aucun scan enregistré. Lance un scan depuis l'onglet
              "Scan now" pour alimenter cette timeline.
            </p>
          )}
          <div className="space-y-1 max-h-[600px] overflow-y-auto">
            {history.data?.map((row) => (
              <button
                key={row.id}
                onClick={() => setSelectedId(row.id)}
                className={cn(
                  "w-full text-left p-2 text-xs border-b border-[color:var(--color-cyber-border)]/30 transition",
                  selectedId === row.id
                    ? "bg-[color:var(--color-cyber-accent)]/5 border-[color:var(--color-cyber-accent)]/40"
                    : "hover:bg-[color:var(--color-cyber-bg-2)]/50",
                )}
              >
                <ScanRow row={row} />
              </button>
            ))}
          </div>
        </section>

        {/* Right : detail */}
        <section className="cyber-card p-3 space-y-2">
          <header className="cyber-label text-[10px] flex items-center justify-between">
            <span className="flex items-center gap-2">
              <Radar className="h-3 w-3" /> détail scan
            </span>
            {selectedId != null && (
              <button
                onClick={() => delMut.mutate(selectedId)}
                disabled={delMut.isPending}
                title="Supprimer ce scan"
                className="cyber-button-ghost p-1 text-[10px]"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            )}
          </header>
          {selectedId == null && (
            <p className="text-xs text-[color:var(--color-cyber-muted)]">
              // sélectionne un scan dans la timeline pour voir les AP
              voisins détectés.
            </p>
          )}
          {detail.isError && (
            <div className="cyber-chip cyber-chip-on px-3 py-2 text-xs">
              {errorMessage(detail.error)}
            </div>
          )}
          {detail.data && <DetailView detail={detail.data} />}
        </section>
      </div>
    </div>
  );
}

function ScanRow({ row }: { row: ScanHistoryRow }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="font-mono">
          <Zap className="inline h-3 w-3 mr-1" /> {row.band} GHz · {row.neighbors_count} AP
          {row.threats_count > 0 && (
            <span className="ml-2 text-amber-300">⚠ {row.threats_count}</span>
          )}
        </span>
        <span className="text-[color:var(--color-cyber-muted)]">
          <Clock className="inline h-3 w-3 mr-1" />
          {new Date(row.started_at).toLocaleString("fr-FR", {
            dateStyle: "short",
            timeStyle: "short",
          })}
        </span>
      </div>
      {row.lat != null && row.lon != null && (
        <div className="text-[10px] text-[color:var(--color-cyber-muted)] flex items-center gap-1">
          <MapPin className="h-3 w-3" />
          <span className="font-mono">
            {row.lat.toFixed(4)}, {row.lon.toFixed(4)}
          </span>
          <span className="ml-1 cyber-chip text-[9px]">{row.source}</span>
        </div>
      )}
    </div>
  );
}

function DetailView({
  detail,
}: {
  detail: import("@/api/scan-history").ScanHistoryDetail;
}) {
  return (
    <div className="space-y-3 text-xs">
      <div className="space-y-1">
        <div>
          <span className="cyber-label">durée:</span>{" "}
          <span className="font-mono">{detail.duration_s.toFixed(1)}s</span>
        </div>
        <div>
          <span className="cyber-label">canal actuel:</span>{" "}
          <span className="font-mono">{detail.current_channel ?? "?"}</span>
        </div>
        <div>
          <span className="cyber-label">recommandé:</span>{" "}
          <span className="cyber-glow font-mono text-emerald-300">
            ch {detail.recommended_channel}
          </span>
        </div>
        {detail.lat != null && detail.lon != null && (
          <div>
            <span className="cyber-label">position:</span>{" "}
            <span className="font-mono">
              {detail.lat.toFixed(6)}, {detail.lon.toFixed(6)}
            </span>{" "}
            <span className="cyber-chip text-[9px]">{detail.source}</span>
          </div>
        )}
      </div>

      <table className="w-full font-mono text-[11px]">
        <thead>
          <tr className="text-[color:var(--color-cyber-muted)] text-left">
            <th className="px-1 py-0.5">SSID</th>
            <th className="px-1 py-0.5">BSSID</th>
            <th className="px-1 py-0.5">ch</th>
            <th className="px-1 py-0.5">RSSI</th>
            <th className="px-1 py-0.5">dist</th>
            <th className="px-1 py-0.5">sec</th>
          </tr>
        </thead>
        <tbody>
          {detail.neighbors.map((n) => {
            const b = bucketFromRssi(n.rssi_dbm);
            const c = bucketColor(b);
            return (
              <tr
                key={n.bssid}
                className="border-t border-[color:var(--color-cyber-border)]/30"
              >
                <td className="px-1 py-0.5">
                  {n.hidden ? (
                    <span className="italic text-[color:var(--color-cyber-muted)]">
                      &lt;hidden&gt;
                    </span>
                  ) : (
                    n.ssid
                  )}
                </td>
                <td className="px-1 py-0.5 text-[10px]">{n.bssid}</td>
                <td className="px-1 py-0.5">{n.channel}</td>
                <td className="px-1 py-0.5">{n.rssi_dbm}</td>
                <td
                  className="px-1 py-0.5 text-[10px]"
                  style={{ color: c }}
                  title={bucketRangeM(b)}
                >
                  {bucketLabel(b)}
                </td>
                <td className="px-1 py-0.5">{n.security}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
