/**
 * RÉSEAU → Radio
 *
 * Per-band radio (layer-1) settings + the channel scanner / planner.
 *
 * Three tabs (2.4 / 5 / 6 GHz). Each tab :
 *   - shows the active config (channel, htmode, txpower, country)
 *   - has a "Scan" button that runs `iw scan` over SSH (~10-25s)
 *   - on scan result : channel scores heatmap + AP list + recommendation
 *   - "Appliquer ch X" pushes the recommendation as the forced channel
 *
 * This page is the operator's RF-tuning console. It does NOT touch SSIDs
 * (those live on Réseau → Radio... wait no, our existing /wifi page is
 * the SSID catalog). For clarity in the cyberpunk UI, the page label is
 * "RF" or "RADIO PLANNER" depending on width.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  RadioTower,
  RefreshCw,
  Zap,
} from "lucide-react";
import {
  getRadioConfigs,
  scanRadio,
  updateRadioConfig,
} from "@/api/wifi-radio";
import DeviceLocationPanel from "@/components/DeviceLocationPanel";
import { errorMessage } from "@/lib/error-utils";
import {
  bucketColor,
  bucketLabel,
  bucketRangeM,
  bucketFromRssi,
} from "@/lib/rssi-distance";
import { cn } from "@/lib/utils";
import type { WifiBand } from "@/types/wifi";
import type {
  ChannelScoreView,
  NeighborAPView,
  ScanResponse,
} from "@/types/wifi-radio";

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

const BANDS: WifiBand[] = ["2", "5", "6"];

export default function NetworksRadio() {
  const qc = useQueryClient();
  const configs = useQuery({
    queryKey: ["wifi", "radios"],
    queryFn: getRadioConfigs,
  });
  const [activeBand, setActiveBand] = useState<WifiBand>("5");
  const [scanResults, setScanResults] = useState<
    Partial<Record<WifiBand, ScanResponse>>
  >({});

  const scanMut = useMutation({
    mutationFn: async (band: WifiBand) => {
      const res = await scanRadio(band);
      return { band, res };
    },
    onSuccess: ({ band, res }) => {
      setScanResults((prev) => ({ ...prev, [band]: res }));
    },
  });

  const applyMut = useMutation({
    mutationFn: async (vars: { band: WifiBand; channel: number }) => {
      return await updateRadioConfig(vars.band, { channel: vars.channel });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["wifi", "radios"] });
    },
  });

  const txMut = useMutation({
    mutationFn: async (vars: { band: WifiBand; txpower_percent: number }) => {
      return await updateRadioConfig(vars.band, {
        txpower_percent: vars.txpower_percent,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["wifi", "radios"] });
    },
  });

  const htMut = useMutation({
    mutationFn: async (vars: { band: WifiBand; htmode: string }) => {
      return await updateRadioConfig(vars.band, { htmode: vars.htmode });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["wifi", "radios"] });
    },
  });

  const countryMut = useMutation({
    mutationFn: async (vars: { band: WifiBand; country: string }) => {
      return await updateRadioConfig(vars.band, { country: vars.country });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["wifi", "radios"] });
    },
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="cyber-display cyber-glow text-2xl">RADIO · PLANNER</h1>
        <p className="cyber-label text-[10px] mt-1">
          Config layer-1 + scanner channel · MTK MT7990 Wi-Fi 7
        </p>
      </header>

      <DeviceLocationPanel />

      {configs.isError && (
        <div className="cyber-chip cyber-chip-on px-3 py-2 text-xs">
          {errorMessage(configs.error)}
        </div>
      )}

      {/* Band tabs */}
      <div className="flex gap-2 border-b border-[color:var(--color-cyber-border)]">
        {BANDS.map((b) => (
          <button
            key={b}
            onClick={() => setActiveBand(b)}
            className={cn(
              "px-4 py-2 text-xs font-mono uppercase tracking-[0.2em] transition",
              activeBand === b
                ? "border-b-2 border-[color:var(--color-cyber-accent)] text-[color:var(--color-cyber-accent)]"
                : "text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-dim)]",
            )}
          >
            {BAND_ICON[b]} · {BAND_LABEL[b]}
          </button>
        ))}
      </div>

      {configs.data ? (
        <BandPanel
          band={activeBand}
          config={configs.data.bands[activeBand]}
          scanResult={scanResults[activeBand]}
          onScan={() => scanMut.mutate(activeBand)}
          scanning={scanMut.isPending && scanMut.variables === activeBand}
          onApplyChannel={(ch) =>
            applyMut.mutate({ band: activeBand, channel: ch })
          }
          onUpdateTx={(p) =>
            txMut.mutate({ band: activeBand, txpower_percent: p })
          }
          onUpdateHtmode={(m) => htMut.mutate({ band: activeBand, htmode: m })}
          onUpdateCountry={(c) =>
            countryMut.mutate({ band: activeBand, country: c })
          }
          mutationsPending={
            applyMut.isPending ||
            txMut.isPending ||
            htMut.isPending ||
            countryMut.isPending
          }
        />
      ) : (
        <div className="text-xs text-[color:var(--color-cyber-muted)]">
          loading…
        </div>
      )}
    </div>
  );
}

function BandPanel({
  band,
  config,
  scanResult,
  onScan,
  scanning,
  onApplyChannel,
  onUpdateTx,
  onUpdateHtmode,
  onUpdateCountry,
  mutationsPending,
}: {
  band: WifiBand;
  config: {
    channel: number;
    htmode: string;
    txpower_percent: number;
    country: string;
    available_htmodes: string[];
  };
  scanResult?: ScanResponse;
  onScan: () => void;
  scanning: boolean;
  onApplyChannel: (ch: number) => void;
  onUpdateTx: (p: number) => void;
  onUpdateHtmode: (m: string) => void;
  onUpdateCountry: (c: string) => void;
  mutationsPending: boolean;
}) {
  return (
    <div className="space-y-4">
      {/* Config card */}
      <section className="cyber-card p-4">
        <header className="cyber-label text-[10px] mb-3 flex items-center gap-2">
          <RadioTower className="h-3 w-3" /> config radio · {BAND_LABEL[band]}
        </header>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
          <FieldGroup label="channel">
            <div className="font-mono text-lg">
              {config.channel === 0 ? "auto (ACS)" : config.channel}
            </div>
          </FieldGroup>
          <FieldGroup label="bande passante">
            <select
              value={config.htmode}
              onChange={(e) => onUpdateHtmode(e.target.value)}
              disabled={mutationsPending}
              className="cyber-input w-full font-mono text-sm"
            >
              {config.available_htmodes.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </FieldGroup>
          <FieldGroup label="puissance (%)">
            <input
              type="range"
              min={10}
              max={100}
              step={5}
              value={config.txpower_percent}
              disabled={mutationsPending}
              onChange={(e) => onUpdateTx(parseInt(e.target.value, 10))}
              className="w-full"
            />
            <div className="font-mono text-sm">
              {config.txpower_percent}%
            </div>
          </FieldGroup>
          <FieldGroup label="country">
            <input
              type="text"
              maxLength={2}
              value={config.country}
              onChange={(e) =>
                onUpdateCountry(e.target.value.toUpperCase().slice(0, 2))
              }
              disabled={mutationsPending}
              className="cyber-input w-20 font-mono text-sm uppercase"
            />
          </FieldGroup>
        </div>
      </section>

      {/* Scanner card */}
      <section className="cyber-card p-4">
        <header className="mb-3 flex items-center justify-between">
          <div className="cyber-label text-[10px] flex items-center gap-2">
            <Zap className="h-3 w-3" /> scanner channel · {BAND_LABEL[band]}
          </div>
          <button
            onClick={onScan}
            disabled={scanning}
            className="cyber-button px-4 py-2 text-xs"
          >
            {scanning ? (
              <>
                <RefreshCw className="mr-2 inline h-3 w-3 animate-spin" />
                scan en cours…
              </>
            ) : (
              <>▶ START SCAN</>
            )}
          </button>
        </header>

        {!scanResult && !scanning && (
          <p className="cyber-label text-[10px]">
            // aucun scan effectué — clique START SCAN pour analyser le spectre
          </p>
        )}

        {scanResult && (
          <ScanResultsView
            result={scanResult}
            onApply={onApplyChannel}
            applyDisabled={mutationsPending}
          />
        )}
      </section>
    </div>
  );
}

function FieldGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="cyber-label text-[9px] mb-1">{label}</div>
      {children}
    </div>
  );
}

function ScanResultsView({
  result,
  onApply,
  applyDisabled,
}: {
  result: ScanResponse;
  onApply: (ch: number) => void;
  applyDisabled: boolean;
}) {
  const maxScore = useMemo(
    () => Math.max(1, ...result.channel_scores.map((s) => s.score)),
    [result.channel_scores],
  );
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 text-xs">
        <div>
          <span className="cyber-label">durée:</span>{" "}
          <span className="font-mono">{result.duration_s.toFixed(1)}s</span>
        </div>
        <div>
          <span className="cyber-label">AP détectés:</span>{" "}
          <span className="font-mono">{result.neighbors.length}</span>
        </div>
        <div>
          <span className="cyber-label">canal actuel:</span>{" "}
          <span className="font-mono">
            {result.current_channel ?? "?"}
          </span>
        </div>
        <div>
          <span className="cyber-label">recommandé:</span>{" "}
          <span className="cyber-glow font-mono text-emerald-300">
            ch {result.recommended_channel}
          </span>
        </div>
      </div>

      {result.threats.length > 0 && (
        <div className="cyber-card cyber-card-accent p-3 space-y-1">
          {result.threats.map((t, i) => (
            <div
              key={i}
              className={cn(
                "text-xs",
                t.level === "alert"
                  ? "text-red-300"
                  : t.level === "warn"
                    ? "text-amber-300"
                    : "text-[color:var(--color-cyber-muted)]",
              )}
            >
              <AlertTriangle className="inline h-3 w-3 mr-1" />
              <span className="font-mono">[{t.kind}]</span> {t.message}
            </div>
          ))}
        </div>
      )}

      <ChannelHeatmap
        scores={result.channel_scores}
        maxScore={maxScore}
        recommended={result.recommended_channel}
        currentChannel={result.current_channel}
        onApply={onApply}
        applyDisabled={applyDisabled}
      />

      <NeighborsTable neighbors={result.neighbors} />
    </div>
  );
}

function ChannelHeatmap({
  scores,
  maxScore,
  recommended,
  currentChannel,
  onApply,
  applyDisabled,
}: {
  scores: ChannelScoreView[];
  maxScore: number;
  recommended: number | null;
  currentChannel: number | null;
  onApply: (ch: number) => void;
  applyDisabled: boolean;
}) {
  return (
    <div className="cyber-card p-3">
      <header className="cyber-label text-[10px] mb-2">
        occupation channels
      </header>
      <table className="w-full font-mono text-xs">
        <thead>
          <tr className="text-[color:var(--color-cyber-muted)] text-left">
            <th className="px-2 py-1">ch</th>
            <th className="px-2 py-1">heatmap</th>
            <th className="px-2 py-1">score</th>
            <th className="px-2 py-1">AP</th>
            <th className="px-2 py-1">notes</th>
            <th className="px-2 py-1"></th>
          </tr>
        </thead>
        <tbody>
          {scores.map((s) => {
            const filled = Math.round((s.score / maxScore) * 10);
            const heat = "█".repeat(filled) + "░".repeat(Math.max(0, 10 - filled));
            const isRec = s.channel === recommended;
            const isCur = s.channel === currentChannel;
            return (
              <tr
                key={s.channel}
                className={cn(
                  "border-t border-[color:var(--color-cyber-border)]/30",
                  isRec && "bg-emerald-500/10",
                  isCur && !isRec && "bg-cyan-500/5",
                )}
              >
                <td className="px-2 py-1 text-[color:var(--color-cyber-accent)]">
                  {s.channel}
                </td>
                <td className="px-2 py-1">
                  <span
                    className={cn(
                      s.score >= 80
                        ? "text-emerald-400"
                        : s.score >= 60
                          ? "text-cyan-400"
                          : s.score >= 40
                            ? "text-amber-400"
                            : "text-red-400",
                    )}
                  >
                    {heat}
                  </span>
                </td>
                <td className="px-2 py-1 font-mono">{s.score}</td>
                <td className="px-2 py-1">{s.neighbor_count}</td>
                <td className="px-2 py-1 text-[10px] text-[color:var(--color-cyber-muted)]">
                  {s.reasons.join(" · ")}
                  {s.is_psc && " · PSC ★"}
                  {s.is_dfs && " · DFS"}
                  {isRec && (
                    <span className="ml-2 text-emerald-300">
                      <CheckCircle2 className="inline h-3 w-3" /> recommandé
                    </span>
                  )}
                  {isCur && (
                    <span className="ml-2 text-cyan-300">◉ actuel</span>
                  )}
                </td>
                <td className="px-2 py-1">
                  <button
                    onClick={() => onApply(s.channel)}
                    disabled={applyDisabled || isCur}
                    className="cyber-button-ghost px-2 py-0.5 text-[10px]"
                    title={
                      isCur
                        ? "ce canal est déjà actif"
                        : `forcer le canal ${s.channel}`
                    }
                  >
                    appliquer
                  </button>
                </td>
              </tr>
            );
          })}
          <tr className="border-t border-[color:var(--color-cyber-border)]/30">
            <td className="px-2 py-1 text-[color:var(--color-cyber-accent)]">
              0
            </td>
            <td className="px-2 py-1 text-[color:var(--color-cyber-muted)] text-[10px]">
              ─── ACS automatique ───
            </td>
            <td colSpan={3} className="px-2 py-1 text-[10px] text-[color:var(--color-cyber-muted)]">
              laisser le driver MTK choisir au boot
            </td>
            <td className="px-2 py-1">
              <button
                onClick={() => onApply(0)}
                disabled={applyDisabled || currentChannel === 0}
                className="cyber-button-ghost px-2 py-0.5 text-[10px]"
              >
                auto
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function NeighborsTable({ neighbors }: { neighbors: NeighborAPView[] }) {
  const sorted = useMemo(
    () => [...neighbors].sort((a, b) => b.rssi_dbm - a.rssi_dbm),
    [neighbors],
  );
  if (sorted.length === 0) {
    return (
      <div className="cyber-card p-3 text-xs text-[color:var(--color-cyber-muted)]">
        Aucun AP voisin détecté sur cette bande.
      </div>
    );
  }
  return (
    <div className="cyber-card p-3">
      <header className="cyber-label text-[10px] mb-2">
        AP voisins ({sorted.length})
      </header>
      <table className="w-full font-mono text-xs">
        <thead>
          <tr className="text-[color:var(--color-cyber-muted)] text-left">
            <th className="px-2 py-1">SSID</th>
            <th className="px-2 py-1">BSSID</th>
            <th className="px-2 py-1">vendor</th>
            <th className="px-2 py-1">ch</th>
            <th className="px-2 py-1">RSSI</th>
            <th className="px-2 py-1">distance</th>
            <th className="px-2 py-1">security</th>
            <th className="px-2 py-1">mode</th>
            <th className="px-2 py-1">flags</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((n) => (
            <tr
              key={n.bssid}
              className={cn(
                "border-t border-[color:var(--color-cyber-border)]/30",
                n.is_ours && "bg-cyan-500/5",
              )}
            >
              <td className="px-2 py-1">
                {n.hidden ? (
                  <span className="text-[color:var(--color-cyber-muted)] italic">
                    &lt;hidden&gt;
                  </span>
                ) : (
                  <span className="text-[color:var(--color-cyber-dim)]">
                    {n.ssid}
                  </span>
                )}
                {n.is_ours && (
                  <span className="ml-2 text-cyan-300 text-[10px]">
                    ← toi
                  </span>
                )}
              </td>
              <td className="px-2 py-1 text-[10px]">{n.bssid}</td>
              <td className="px-2 py-1 text-[10px]">
                <VendorBadge
                  vendor={n.vendor}
                  vendorSlug={n.vendor_slug}
                  isRandomized={n.is_randomized}
                />
              </td>
              <td className="px-2 py-1">{n.channel}</td>
              <td
                className={cn(
                  "px-2 py-1",
                  n.rssi_dbm > -55
                    ? "text-emerald-300"
                    : n.rssi_dbm > -75
                      ? "text-cyan-300"
                      : "text-[color:var(--color-cyber-muted)]",
                )}
              >
                {n.rssi_dbm} dBm
              </td>
              <td className="px-2 py-1">
                <DistanceBadge rssi_dbm={n.rssi_dbm} />
              </td>
              <td className="px-2 py-1">{n.security}</td>
              <td className="px-2 py-1">{n.ht_mode}</td>
              <td className="px-2 py-1 text-[10px]">
                {n.is_wps_enabled && (
                  <span className="text-amber-300">WPS</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VendorBadge({
  vendor,
  vendorSlug,
  isRandomized,
}: {
  vendor: string;
  vendorSlug: string;
  isRandomized: boolean;
}) {
  if (isRandomized) {
    return (
      <span
        title="MAC locally administered (U/L bit) — typique randomisation iOS/Android/Pwnagotchi"
        className="text-amber-300"
      >
        🎭 random
      </span>
    );
  }
  if (!vendor) {
    return (
      <span className="text-[color:var(--color-cyber-muted)]" title="OUI inconnu (registre IEEE non chargé ou OUI non répertorié)">
        ?
      </span>
    );
  }
  // Truncate long vendor names ; keep slug as a data attribute for
  // future logo placement.
  const display = vendor.length > 18 ? vendor.slice(0, 16) + "…" : vendor;
  return (
    <span title={vendor} data-vendor-slug={vendorSlug}>
      {display}
    </span>
  );
}

function DistanceBadge({ rssi_dbm }: { rssi_dbm: number }) {
  const b = bucketFromRssi(rssi_dbm);
  const color = bucketColor(b);
  return (
    <span
      title={`Estimation grossière depuis RSSI · ${bucketRangeM(b)}`}
      className="inline-flex items-center gap-1 text-[10px] font-mono"
      style={{ color }}
    >
      <span
        className="inline-block h-2 w-2 rounded-full"
        style={{ background: color, boxShadow: `0 0 6px ${color}` }}
      />
      {bucketLabel(b)}
    </span>
  );
}
