// Ambient WiFi scan control page (Q2-A).
//
// Per-band card lets the operator toggle a background scan loop on/off,
// set the interval + retention, see the last-run heartbeat, and trigger
// a one-shot pass to validate the config.

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Clock,
  PlayCircle,
  RadioTower,
  RefreshCw,
  XCircle,
  Zap,
} from "lucide-react";

import {
  type AmbientConfig,
  AMBIENT_INTERVALS,
  listAmbientConfigs,
  listRecentAmbientScans,
  runAmbientNow,
  upsertAmbientConfig,
} from "@/api/ambient-scan";
import { cn } from "@/lib/utils";
import type { WifiBand } from "@/types/wifi";

const BAND_LABEL: Record<WifiBand, string> = {
  "2": "2.4 GHz",
  "5": "5 GHz",
  "6": "6 GHz",
};
const BAND_ICON: Record<WifiBand, string> = {
  "2": "RA",
  "5": "RAI",
  "6": "RAX",
};

export default function AmbientPage() {
  const qc = useQueryClient();
  const configs = useQuery({
    queryKey: ["wifi", "ambient", "configs"],
    queryFn: () => listAmbientConfigs(),
    // Refresh while we're on the page so last_run_at + counts stay live.
    refetchInterval: 15_000,
  });
  const recent = useQuery({
    queryKey: ["wifi", "ambient", "recent"],
    queryFn: () => listRecentAmbientScans(20),
    refetchInterval: 15_000,
  });

  return (
    <div className="space-y-4">
      <header className="cyber-label flex items-center gap-2">
        <RadioTower className="h-3 w-3" /> scan ambient · arrière-plan
      </header>
      <p className="text-xs text-[color:var(--color-cyber-muted)] max-w-2xl">
        Lance un scan WiFi en arrière-plan à intervalle r&eacute;gulier. Chaque passe
        est persist&eacute;e comme un scan normal (visible dans la liste d&eacute;roulante
        de la page Radio). La r&eacute;tention purge automatiquement les anciennes
        passes — les scans manuels ne sont jamais touch&eacute;s.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {(["2", "5", "6"] as WifiBand[]).map((band) => {
          const cfg = configs.data?.find((c) => c.band === band);
          return (
            <AmbientBandCard
              key={band}
              band={band}
              cfg={cfg}
              onSave={async (body) => {
                await upsertAmbientConfig(band, body);
                qc.invalidateQueries({ queryKey: ["wifi", "ambient"] });
              }}
              onRunNow={async () => {
                const res = await runAmbientNow(band);
                qc.invalidateQueries({ queryKey: ["wifi", "ambient"] });
                return res;
              }}
            />
          );
        })}
      </div>

      <section className="cyber-card p-3">
        <header className="cyber-label text-[10px] mb-2 flex items-center gap-2">
          <Clock className="h-3 w-3" /> derni&egrave;res passes ambient
        </header>
        {recent.data && recent.data.length > 0 ? (
          <table className="w-full font-mono text-[11px]">
            <thead>
              <tr className="text-[color:var(--color-cyber-muted)] text-left">
                <th className="px-2 py-1">id</th>
                <th className="px-2 py-1">bande</th>
                <th className="px-2 py-1">quand</th>
                <th className="px-2 py-1">voisins</th>
                <th className="px-2 py-1">durée</th>
              </tr>
            </thead>
            <tbody>
              {recent.data.map((r) => (
                <tr
                  key={r.id}
                  className="border-t border-[color:var(--color-cyber-border)]/30"
                >
                  <td className="px-2 py-0.5">#{r.id}</td>
                  <td className="px-2 py-0.5">{BAND_LABEL[r.band]}</td>
                  <td className="px-2 py-0.5 text-[color:var(--color-cyber-muted)]">
                    {new Date(r.started_at).toLocaleString("fr-FR")}
                  </td>
                  <td className="px-2 py-0.5">{r.neighbors_count}</td>
                  <td className="px-2 py-0.5 text-[10px]">
                    {r.duration_s.toFixed(1)}s
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-xs text-[color:var(--color-cyber-muted)]">
            Aucune passe ambient enregistrée pour le moment.
          </p>
        )}
      </section>
    </div>
  );
}

function AmbientBandCard({
  band,
  cfg,
  onSave,
  onRunNow,
}: {
  band: WifiBand;
  cfg: AmbientConfig | undefined;
  onSave: (body: {
    enabled: boolean;
    interval_s: number;
    retention_days: number;
  }) => Promise<void>;
  onRunNow: () => Promise<{ status: string; neighbors?: number | null }>;
}) {
  const [enabled, setEnabled] = useState<boolean>(cfg?.enabled ?? false);
  const [intervalS, setIntervalS] = useState<number>(cfg?.interval_s ?? 60);
  const [retentionDays, setRetentionDays] = useState<number>(
    cfg?.retention_days ?? 7,
  );

  // Re-sync local state when the server payload changes (e.g. refetch).
  useMemo(() => {
    if (!cfg) return;
    setEnabled(cfg.enabled);
    setIntervalS(cfg.interval_s);
    setRetentionDays(cfg.retention_days);
  }, [cfg?.enabled, cfg?.interval_s, cfg?.retention_days]);

  const dirty =
    enabled !== (cfg?.enabled ?? false) ||
    intervalS !== (cfg?.interval_s ?? 60) ||
    retentionDays !== (cfg?.retention_days ?? 7);

  const saveMut = useMutation({
    mutationFn: () =>
      onSave({
        enabled,
        interval_s: intervalS,
        retention_days: retentionDays,
      }),
  });
  const runMut = useMutation({
    mutationFn: onRunNow,
  });

  // Daily ambient row count ≈ 86400 / interval_s. With retention_days,
  // total accumulated ≈ count × retention_days. Each row ≈ 20 voisins
  // × 250 bytes = ~5 KB. Cheap rough estimate.
  const projectedDailyCount = Math.floor(86400 / intervalS);
  const projectedTotalKB = Math.round(
    (projectedDailyCount * retentionDays * 5) / 1,
  );

  const lastRunDate = cfg?.last_run_at
    ? new Date(cfg.last_run_at)
    : null;
  const lastRunMinAgo = lastRunDate
    ? Math.floor((Date.now() - lastRunDate.getTime()) / 60_000)
    : null;

  return (
    <div className="cyber-card p-4 space-y-3">
      <header className="flex items-center justify-between">
        <div className="cyber-label text-[10px] flex items-center gap-2">
          <Zap className="h-3 w-3" /> {BAND_ICON[band]} · {BAND_LABEL[band]}
        </div>
        <StatusBadge enabled={cfg?.enabled} status={cfg?.last_status} />
      </header>

      <label className="flex items-center gap-2 text-xs cursor-pointer">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="cyber-checkbox"
        />
        <span>Activer le scan en arrière-plan</span>
      </label>

      <div className={cn(!enabled && "opacity-50 pointer-events-none")}>
        <div>
          <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
            intervalle
          </label>
          <select
            value={intervalS}
            onChange={(e) => setIntervalS(Number(e.target.value))}
            className="cyber-input w-full text-xs"
          >
            {AMBIENT_INTERVALS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label} — {opt.hint}
              </option>
            ))}
          </select>
        </div>

        <div className="mt-3">
          <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
            r&eacute;tention&nbsp;
            <span className="text-[color:var(--color-cyber-accent)]">
              {retentionDays} jour{retentionDays > 1 ? "s" : ""}
            </span>
          </label>
          <input
            type="range"
            min={1}
            max={90}
            value={retentionDays}
            onChange={(e) => setRetentionDays(Number(e.target.value))}
            className="w-full"
          />
        </div>

        <div className="mt-3 text-[10px] text-[color:var(--color-cyber-muted)] font-mono">
          ≈ {projectedDailyCount} passes/jour · ~{projectedTotalKB} KB total accumulé
        </div>
      </div>

      <div className="border-t border-[color:var(--color-cyber-border)]/40 pt-3 space-y-2">
        <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
          {cfg?.last_run_at ? (
            <>
              dernière passe&nbsp;:{" "}
              <span className="font-mono">
                il y a {lastRunMinAgo}min
              </span>
            </>
          ) : (
            "jamais lancé"
          )}
          {cfg && cfg.persisted_scans_24h > 0 && (
            <>
              {" · "}
              <span className="font-mono">
                {cfg.persisted_scans_24h}× / 24h
              </span>
              {" · "}
              <span className="font-mono">
                {cfg.persisted_scans_total} total
              </span>
            </>
          )}
        </div>
        {cfg?.last_status === "error" && cfg.last_error && (
          <div className="text-[10px] text-amber-300 font-mono break-all">
            ⚠ {cfg.last_error}
          </div>
        )}
        {runMut.data && (
          <div className="text-[10px] text-emerald-300 font-mono">
            ✓ {runMut.data.status} ({runMut.data.neighbors ?? "?"} voisins)
          </div>
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => saveMut.mutate()}
          disabled={!dirty || saveMut.isPending}
          className="cyber-button px-3 py-1.5 text-xs flex-1 disabled:opacity-30"
        >
          {saveMut.isPending ? "…" : dirty ? "enregistrer" : "à jour"}
        </button>
        <button
          onClick={() => runMut.mutate()}
          disabled={runMut.isPending}
          className="cyber-button-ghost px-3 py-1.5 text-xs"
          title="Lancer une passe maintenant pour valider la config"
        >
          {runMut.isPending ? (
            <RefreshCw className="h-3 w-3 animate-spin" />
          ) : (
            <PlayCircle className="h-3 w-3" />
          )}
        </button>
      </div>
    </div>
  );
}

function StatusBadge({
  enabled,
  status,
}: {
  enabled: boolean | undefined;
  status: string | undefined;
}) {
  if (!enabled) {
    return (
      <span className="text-[9px] cyber-chip text-[color:var(--color-cyber-muted)]">
        OFF
      </span>
    );
  }
  if (status === "ok") {
    return (
      <span className="inline-flex items-center gap-1 text-[9px] text-emerald-300">
        <CheckCircle2 className="h-3 w-3" /> OK
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="inline-flex items-center gap-1 text-[9px] text-amber-300">
        <XCircle className="h-3 w-3" /> ERR
      </span>
    );
  }
  return (
    <span className="text-[9px] cyber-chip text-cyan-300">RUNNING</span>
  );
}
