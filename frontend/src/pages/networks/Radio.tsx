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

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Clock,
  History,
  MapPin,
  RadioTower,
  RefreshCw,
  Trash2,
  Zap,
} from "lucide-react";
import {
  getRadioConfigs,
  scanRadio,
  updateRadioConfig,
} from "@/api/wifi-radio";
import {
  deleteScanHistory,
  getScanHistoryDetail,
  listScanHistory,
  type ScanHistoryDetail,
  type ScanHistoryRow,
} from "@/api/scan-history";
import ApReviewModal, { type ReviewSeed } from "@/components/ApReviewModal";
import DeviceLocationPanel from "@/components/DeviceLocationPanel";
import SpectrumChart from "@/components/SpectrumChart";
import VendorLogo from "@/components/VendorLogo";
import { type ReviewStatus, statusConfig } from "@/api/ap-reviews";
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
  PhysicalAPGroupView,
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

// State of a displayed scan : may be a fresh run that we just launched,
// or a past run loaded from scan_history. The shape is the same — only
// difference is the optional ``loadedFromId`` marker so the UI can show
// "scan rechargé · 03/06 14:32" instead of pretending it's a live run.
interface DisplayedScan {
  result: ScanResponse;
  loadedFromId?: number;
  loadedAt?: string;       // ISO timestamp of the original scan
  loadedNote?: string;
}

export default function NetworksRadio() {
  const qc = useQueryClient();
  const configs = useQuery({
    queryKey: ["wifi", "radios"],
    queryFn: getRadioConfigs,
  });
  const [activeBand, setActiveBand] = useState<WifiBand>("5");
  const [displayed, setDisplayed] = useState<
    Partial<Record<WifiBand, DisplayedScan>>
  >({});

  // Per-band scan duration choice. 0 = single pass (default).
  // Persisted across band switches so the operator's choice sticks.
  const [scanDurationS, setScanDurationS] = useState<number>(0);
  const scanMut = useMutation({
    mutationFn: async (band: WifiBand) => {
      const res = await scanRadio(band, { durationS: scanDurationS });
      return { band, res };
    },
    onSuccess: ({ band, res }) => {
      setDisplayed((prev) => ({ ...prev, [band]: { result: res } }));
      qc.invalidateQueries({ queryKey: ["scan-history"] });
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
          displayed={displayed[activeBand]}
          onScan={() => scanMut.mutate(activeBand)}
          onLoadScan={(loaded) =>
            setDisplayed((prev) => ({ ...prev, [activeBand]: loaded }))
          }
          onClearLoaded={() =>
            setDisplayed((prev) => ({ ...prev, [activeBand]: undefined }))
          }
          scanning={scanMut.isPending && scanMut.variables === activeBand}
          scanDurationS={scanDurationS}
          onChangeScanDuration={setScanDurationS}
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
  displayed,
  onScan,
  onLoadScan,
  onClearLoaded,
  scanning,
  scanDurationS,
  onChangeScanDuration,
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
  displayed?: DisplayedScan;
  onScan: () => void;
  onLoadScan: (d: DisplayedScan) => void;
  onClearLoaded: () => void;
  scanning: boolean;
  scanDurationS: number;
  onChangeScanDuration: (s: number) => void;
  onApplyChannel: (ch: number) => void;
  onUpdateTx: (p: number) => void;
  onUpdateHtmode: (m: string) => void;
  onUpdateCountry: (c: string) => void;
  mutationsPending: boolean;
}) {
  const scanResult = displayed?.result;
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
          <div className="flex items-center gap-2">
            <ScanPicker
              band={band}
              onLoad={onLoadScan}
              activeLoadedId={displayed?.loadedFromId}
            />
            <ScanDurationSelect
              value={scanDurationS}
              onChange={onChangeScanDuration}
              disabled={scanning}
            />
            <button
              onClick={onScan}
              disabled={scanning}
              className="cyber-button px-4 py-2 text-xs"
            >
              {scanning ? (
                <ScanningButtonContent durationS={scanDurationS} />
              ) : (
                <>▶ START SCAN</>
              )}
            </button>
          </div>
        </header>

        {displayed?.loadedFromId && (
          <div className="flex items-center gap-3 cyber-card cyber-card-accent p-2 text-xs">
            <History className="h-3 w-3" />
            <span>
              Scan rechargé{" "}
              <span className="font-mono">
                #{displayed.loadedFromId}
              </span>
              {displayed.loadedAt && (
                <span className="ml-2 text-[color:var(--color-cyber-muted)]">
                  · {new Date(displayed.loadedAt).toLocaleString("fr-FR")}
                </span>
              )}
              {displayed.loadedNote && (
                <span className="ml-2 italic text-[color:var(--color-cyber-muted)]">
                  · {displayed.loadedNote}
                </span>
              )}
            </span>
            <button
              onClick={onClearLoaded}
              className="ml-auto cyber-button-ghost px-2 py-0.5 text-[10px]"
            >
              fermer
            </button>
          </div>
        )}

        {!scanResult && !scanning && (
          <p className="cyber-label text-[10px]">
            // aucun scan affiché — clique START SCAN ou pioche un scan
            précédent dans la liste déroulante
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
          <span className="cyber-label">SSIDs détectés:</span>{" "}
          <span className="font-mono">{result.neighbors.length}</span>
          {result.physical_aps.length > 0 && (
            <>
              {" "}
              <span className="text-[color:var(--color-cyber-muted)]">
                sur
              </span>{" "}
              <span className="font-mono text-[color:var(--color-cyber-accent)]">
                {result.physical_aps.length}
              </span>{" "}
              <span className="text-[color:var(--color-cyber-muted)]">
                AP physique{result.physical_aps.length > 1 ? "s" : ""}
              </span>
            </>
          )}
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

      <APTreeView
        neighbors={result.neighbors}
        groups={result.physical_aps}
        band={result.band}
      />

      <SpectrumChart
        neighbors={result.neighbors}
        band={result.band}
        currentChannel={result.current_channel ?? undefined}
        recommendedChannel={result.recommended_channel ?? undefined}
      />
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

// Distinct color per ap_root group so the operator visually clusters
// VAPs of the same physical radio at a glance. Returns a stable hash
// → hue so the same ap_root always paints in the same shade.
function colorForApRoot(apRoot: string): string {
  if (!apRoot) return "transparent";
  let h = 0;
  for (let i = 0; i < apRoot.length; i++) {
    h = (h * 31 + apRoot.charCodeAt(i)) >>> 0;
  }
  const hue = h % 360;
  return `hsl(${hue} 70% 55%)`;
}

type ReviewFilter = "all" | "no_review" | "trusted" | "suspicious" | "ignored";

function APTreeView({
  neighbors,
  groups,
  band,
}: {
  neighbors: NeighborAPView[];
  groups: PhysicalAPGroupView[];
  band: WifiBand;
}) {
  const [filter, setFilter] = useState<ReviewFilter>("all");
  // The modal accepts either a group or a per-BSSID seed, built on demand.
  const [reviewSeed, setReviewSeed] = useState<ReviewSeed | null>(null);
  const openGroupReview = (g: PhysicalAPGroupView) =>
    setReviewSeed({
      scope: "group",
      ap_root: g.ap_root,
      vendor: g.vendor,
      ssids: g.ssids,
      bssids: g.bssids,
      band,
      channel: g.channel,
      current_status: g.review_status,
      current_label: g.review_label,
    });
  const openBssidReview = (
    g: PhysicalAPGroupView,
    n: NeighborAPView,
  ) =>
    setReviewSeed({
      scope: "bssid",
      ap_root: g.ap_root,
      bssid: n.bssid,
      ssid: n.ssid,
      vendor: n.vendor || g.vendor,
      ssids: [],
      bssids: [],
      band,
      channel: n.channel || g.channel,
      current_status: n.review_status_own ?? null,
      current_label: n.review_label_own ?? "",
      inherited_group_status: g.review_status,
      inherited_group_label: g.review_label,
    });

  // Apply review filter on top of the original group list.
  const filteredGroups = useMemo(() => {
    if (filter === "all") {
      // Hide ignored APs from default view (still kept in history).
      return groups.filter((g) => g.review_status !== "ignored");
    }
    if (filter === "no_review")
      return groups.filter((g) => !g.review_status);
    return groups.filter((g) => g.review_status === filter);
  }, [groups, filter]);

  // Group label G1/G2/G3 mapped from order-of-strength on the FILTERED
  // list so labels match what's actually shown.
  const groupLabel = useMemo(() => {
    const m = new Map<string, string>();
    filteredGroups.forEach((g, i) => m.set(g.ap_root, `G${i + 1}`));
    return m;
  }, [filteredGroups]);

  // Members per group, sorted by best RSSI first within each.
  const membersByRoot = useMemo(() => {
    const m = new Map<string, NeighborAPView[]>();
    for (const n of neighbors) {
      const k = n.ap_root || n.bssid;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(n);
    }
    for (const list of m.values()) {
      list.sort((a, b) => b.rssi_dbm - a.rssi_dbm);
    }
    return m;
  }, [neighbors]);

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (root: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(root)) next.delete(root);
      else next.add(root);
      return next;
    });
  const allExpanded =
    expanded.size === filteredGroups.length && filteredGroups.length > 0;
  const toggleAll = () => {
    if (allExpanded) setExpanded(new Set());
    else setExpanded(new Set(filteredGroups.map((g) => g.ap_root)));
  };

  // Count per status — used in the filter dropdown chip.
  const counts = useMemo(() => {
    const c: Record<string, number> = {
      all: groups.length,
      no_review: 0,
      trusted: 0,
      suspicious: 0,
      ignored: 0,
      known: 0,
    };
    for (const g of groups) {
      if (!g.review_status) c.no_review = (c.no_review ?? 0) + 1;
      else c[g.review_status] = (c[g.review_status] ?? 0) + 1;
    }
    return c;
  }, [groups]);

  if (groups.length === 0) {
    return (
      <div className="cyber-card p-3 text-xs text-[color:var(--color-cyber-muted)]">
        Aucun AP voisin détecté sur cette bande.
      </div>
    );
  }

  return (
    <div className="cyber-card p-3 space-y-1">
      <header className="cyber-label text-[10px] mb-2 flex items-center justify-between gap-2 flex-wrap">
        <span>
          AP physiques ({filteredGroups.length}/{groups.length}) · VAPs ({neighbors.length})
        </span>
        <div className="flex items-center gap-2">
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value as ReviewFilter)}
            className="cyber-input text-[10px] py-0.5 px-2"
            title="Filtre par statut de review"
          >
            <option value="all">tous ({counts.all})</option>
            <option value="no_review">sans avis ({counts.no_review})</option>
            <option value="trusted">✓ trusted ({counts.trusted})</option>
            <option value="suspicious">⚠ suspicious ({counts.suspicious})</option>
            <option value="ignored">⊘ ignored ({counts.ignored})</option>
          </select>
          <button
            onClick={toggleAll}
            className="cyber-button-ghost px-2 py-0.5 text-[9px]"
          >
            {allExpanded ? "tout replier" : "tout déplier"}
          </button>
        </div>
      </header>
      {filteredGroups.map((g) => {
        const isOpen = expanded.has(g.ap_root);
        const members = membersByRoot.get(g.ap_root) ?? [];
        const label = groupLabel.get(g.ap_root) ?? "";
        const color = colorForApRoot(g.ap_root);
        const rev = statusConfig(g.review_status as ReviewStatus | null);
        return (
          <div key={g.ap_root}>
            <div
              className="w-full flex items-center gap-2 p-2 text-xs hover:bg-[color:var(--color-cyber-bg-2)]/40 border-b border-[color:var(--color-cyber-border)]/30"
              style={{ borderLeft: `3px solid ${color}` }}
            >
              <button
                onClick={() => toggle(g.ap_root)}
                className="flex-1 min-w-0 flex items-center gap-2 text-left"
                title="Déplier / replier les VAPs"
              >
              {isOpen ? (
                <ChevronDown className="h-3 w-3 shrink-0" />
              ) : (
                <ChevronRight className="h-3 w-3 shrink-0" />
              )}
              <span
                className="px-1.5 py-0.5 rounded font-mono font-bold text-[10px]"
                style={{ background: color, color: "white" }}
              >
                {label}
              </span>
              <span className="font-mono text-[color:var(--color-cyber-accent)]">
                {g.member_count} SSID{g.member_count > 1 ? "s" : ""}
              </span>
              {g.hidden_count > 0 && (
                <span className="text-[10px] text-[color:var(--color-cyber-muted)]">
                  ({g.member_count - g.hidden_count} visibles + {g.hidden_count} cachés)
                </span>
              )}
              <span className="text-[color:var(--color-cyber-muted)] text-[10px]">
                {g.channels && g.channels.length > 0
                  ? `ch ${g.channels.join(", ")}`
                  : `ch ${g.channel}`}
                {g.bands && g.bands.length > 1 && (
                  <span className="ml-1 text-[color:var(--color-cyber-accent)]">
                    · {g.bands.join("/")} GHz
                  </span>
                )}
              </span>
              <DistanceBadge rssi_dbm={g.rssi_dbm} />
              <span className="font-mono text-[10px]">{g.rssi_dbm} dBm</span>
              <span className="ml-auto text-[10px] flex items-center gap-2">
                {rev && (
                  <span
                    className="font-mono text-[10px] px-1.5 py-0.5 rounded"
                    style={{
                      color: rev.color,
                      border: `1px solid ${rev.color}66`,
                      background: `${rev.color}11`,
                    }}
                    title={`${rev.hint}${g.review_label ? ` · ${g.review_label}` : ""}`}
                  >
                    {rev.icon} {g.review_label || rev.label}
                  </span>
                )}
                <VendorLogo
                  slug={g.vendor_slug}
                  vendor={g.vendor}
                  isRandomized={g.is_all_randomized}
                  withLabel
                  size="sm"
                />
                {g.has_wps && (
                  <span className="text-amber-300 text-[9px]">WPS</span>
                )}
              </span>
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  openGroupReview(g);
                }}
                className="cyber-button-ghost px-2 py-0.5 text-[9px] shrink-0"
                title="Évaluer / éditer la review du groupe (AP physique)"
              >
                {g.review_status ? "éditer" : "review"}
              </button>
            </div>
            {isOpen && (
              <div className="ml-6 mb-2">
                <table className="w-full font-mono text-[11px]">
                  <thead>
                    <tr className="text-[color:var(--color-cyber-muted)] text-left">
                      <th className="px-2 py-1">SSID</th>
                      <th className="px-2 py-1">BSSID</th>
                      <th className="px-2 py-1">ch</th>
                      <th className="px-2 py-1">band</th>
                      <th className="px-2 py-1">vendor</th>
                      <th className="px-2 py-1">RSSI</th>
                      <th className="px-2 py-1">distance</th>
                      <th className="px-2 py-1">security</th>
                      <th className="px-2 py-1">mode</th>
                      <th className="px-2 py-1">flags</th>
                      <th className="px-2 py-1">avis</th>
                      <th className="px-2 py-1"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((n) => (
                      <tr
                        key={n.bssid}
                        className="border-t border-[color:var(--color-cyber-border)]/30"
                      >
                        <td className="px-2 py-0.5">
                          {n.hidden ? (
                            <span className="text-[color:var(--color-cyber-muted)] italic">
                              &lt;hidden&gt;
                            </span>
                          ) : (
                            n.ssid
                          )}
                        </td>
                        <td className="px-2 py-0.5 text-[10px]">{n.bssid}</td>
                        <td className="px-2 py-0.5 text-[10px] font-mono">
                          {n.channel}
                        </td>
                        <td className="px-2 py-0.5 text-[10px] font-mono text-[color:var(--color-cyber-muted)]">
                          {n.band}
                        </td>
                        <td className="px-2 py-0.5 text-[10px]">
                          <VendorLogo
                            slug={n.vendor_slug}
                            vendor={n.vendor}
                            isRandomized={n.is_randomized}
                            withLabel
                            size="sm"
                          />
                        </td>
                        <td
                          className={cn(
                            "px-2 py-0.5",
                            n.rssi_dbm > -55
                              ? "text-emerald-300"
                              : n.rssi_dbm > -75
                                ? "text-cyan-300"
                                : "text-[color:var(--color-cyber-muted)]",
                          )}
                        >
                          {n.rssi_dbm} dBm
                        </td>
                        <td className="px-2 py-0.5">
                          <DistanceBadge rssi_dbm={n.rssi_dbm} />
                        </td>
                        <td className="px-2 py-0.5">{n.security}</td>
                        <td className="px-2 py-0.5">{n.ht_mode}</td>
                        <td className="px-2 py-0.5 text-[10px]">
                          <div className="inline-flex items-center gap-1">
                            {n.is_wps_enabled && (
                              <span className="text-amber-300">WPS</span>
                            )}
                            {n.is_randomized && (
                              <span
                                className="text-amber-300"
                                title="MAC randomisée (U/L bit)"
                              >
                                🎭
                              </span>
                            )}
                            <VAPSeenBadge n={n} />
                          </div>
                        </td>
                        <td className="px-2 py-0.5 text-[10px]">
                          <VAPStatusBadge n={n} />
                        </td>
                        <td className="px-2 py-0.5 text-[10px]">
                          <button
                            onClick={() => openBssidReview(g, n)}
                            className="cyber-button-ghost px-1.5 py-0.5 text-[9px]"
                            title={
                              n.review_status_own
                                ? "Éditer l'override BSSID"
                                : "Override BSSID (remplace le statut du groupe pour ce BSSID uniquement)"
                            }
                          >
                            {n.review_status_own ? "éditer" : "override"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
      <ApReviewModal
        open={reviewSeed !== null}
        onClose={() => setReviewSeed(null)}
        seed={reviewSeed}
      />
    </div>
  );
}

/** Effective-status badge for one VAP row. Atténué (italique + dim border)
 *  quand le statut est hérité du groupe, plein quand c'est un override
 *  propre au BSSID. */
function VAPStatusBadge({ n }: { n: NeighborAPView }) {
  const own = n.review_status_own;
  const effective = n.review_status_effective;
  if (!effective) {
    return <span className="text-[color:var(--color-cyber-muted)]">—</span>;
  }
  const rev = statusConfig(effective as ReviewStatus);
  if (!rev) return null;
  const inherited = !own;
  return (
    <span
      className={cn(
        "font-mono text-[10px] px-1.5 py-0.5 rounded inline-flex items-center gap-1",
        inherited && "italic",
      )}
      style={{
        color: rev.color,
        border: `1px ${inherited ? "dashed" : "solid"} ${rev.color}${inherited ? "55" : "aa"}`,
        background: inherited ? "transparent" : `${rev.color}14`,
        opacity: inherited ? 0.85 : 1,
      }}
      title={
        inherited
          ? `hérité du groupe — ${rev.hint}`
          : `override BSSID — ${rev.hint}`
      }
    >
      {rev.icon} {rev.label}
      {inherited && (
        <span className="text-[8px] opacity-70">hérité</span>
      )}
    </span>
  );
}


// Convert a persisted scan history detail back into the in-memory
// ScanResponse shape so the same view code renders both fresh and
// loaded scans. channel_scores + threats aren't persisted ; they
// render as "—" / empty when we look at a past scan.
function historyToDisplayed(d: ScanHistoryDetail): DisplayedScan {
  const neighbors: NeighborAPView[] = d.neighbors.map((n) => ({
    ...n,
    band: n.band as WifiBand,
    is_ours: false,
  }));
  // Trust the server: backend already rebuilt groups from the persisted
  // neighbours AND overlaid the operator's review status.
  const groups: PhysicalAPGroupView[] = (d.physical_aps ?? []).map((g) => ({
    ...g,
    review_status: g.review_status ?? null,
  }));
  groups.sort((a, b) => b.rssi_dbm - a.rssi_dbm);
  const result: ScanResponse = {
    band: d.band as WifiBand,
    iface: d.iface,
    duration_s: d.duration_s,
    started_at: new Date(d.started_at).getTime() / 1000,
    neighbors,
    // Recomputed server-side from persisted neighbours, so the heat-map
    // restores identically when an old scan is reloaded.
    channel_scores: d.channel_scores ?? [],
    recommended_channel: d.recommended_channel,
    current_channel: d.current_channel,
    threats: [],
    physical_aps: groups,
  };
  const locParts: string[] = [];
  if (d.lat != null && d.lon != null) {
    locParts.push(`${d.lat.toFixed(4)}, ${d.lon.toFixed(4)}`);
    if (d.source) locParts.push(d.source);
  }
  return {
    result,
    loadedFromId: d.id,
    loadedAt: d.started_at,
    loadedNote: locParts.join(" · "),
  };
}

function ScanPicker({
  band,
  onLoad,
  activeLoadedId,
}: {
  band: WifiBand;
  onLoad: (d: DisplayedScan) => void;
  activeLoadedId?: number;
}) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["scan-history", band, "for-picker"],
    queryFn: () => listScanHistory({ band, limit: 50 }),
  });
  const delMut = useMutation({
    mutationFn: (id: number) => deleteScanHistory(id),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["scan-history"] }),
  });
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  // Recompute position whenever the dropdown opens or the window resizes.
  useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      const r = btnRef.current?.getBoundingClientRect();
      if (!r) return;
      setPos({ top: r.bottom + 4, right: window.innerWidth - r.right });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);
  const handlePick = async (row: ScanHistoryRow) => {
    setOpen(false);
    try {
      const detail = await getScanHistoryDetail(row.id);
      onLoad(historyToDisplayed(detail));
    } catch (e) {
      console.error("Failed to load scan", e);
    }
  };
  return (
    <div className="relative">
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={list.isLoading}
        className="cyber-button-ghost px-3 py-2 text-xs"
        title="Recharger un scan précédent"
      >
        <History className="inline h-3 w-3 mr-1" />
        scans précédents ({list.data?.length ?? 0})
        <ChevronDown className="inline h-3 w-3 ml-1" />
      </button>
      {open && pos && createPortal(
        <>
          {/* Click-outside backdrop */}
          <div
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-40"
          />
          <div
            className="fixed z-50 w-[440px] max-h-[420px] overflow-y-auto rounded-sm shadow-2xl"
            style={{
              top: pos.top,
              right: pos.right,
              background: "var(--color-cyber-surface)",
              border: "1px solid var(--color-cyber-border)",
              color: "var(--color-cyber-fg)",
            }}
          >
          <div className="flex items-center justify-between px-3 py-2 border-b border-[color:var(--color-cyber-border)]/60 sticky top-0 bg-[color:var(--color-cyber-surface)] z-10">
            <span className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)]">
              scans {BAND_LABEL[band]} précédents
            </span>
            <button
              onClick={() => setOpen(false)}
              className="text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] text-xs"
              title="Fermer"
            >
              ✕
            </button>
          </div>
          {list.data && list.data.length === 0 && (
            <p className="text-xs text-[color:var(--color-cyber-muted)] p-3">
              aucun scan {BAND_LABEL[band]} enregistré encore
            </p>
          )}
          {list.data?.map((row) => (
            <div
              key={row.id}
              className={cn(
                "flex items-start gap-2 px-3 py-2 text-xs border-b border-[color:var(--color-cyber-border)]/30 hover:bg-[color:var(--color-cyber-bg-2)]/60",
                row.id === activeLoadedId && "bg-[color:var(--color-cyber-accent)]/10",
              )}
            >
              <button
                onClick={() => handlePick(row)}
                className="flex-1 min-w-0 text-left"
              >
                <div className="font-mono flex items-center gap-2 flex-wrap">
                  <span className="text-[color:var(--color-cyber-fg)]">
                    #{row.id}
                  </span>
                  <span className="text-[color:var(--color-cyber-muted)]">·</span>
                  <span>{row.neighbors_count} SSIDs</span>
                  {row.threats_count > 0 && (
                    <span className="text-amber-300">⚠ {row.threats_count}</span>
                  )}
                  {row.id === activeLoadedId && (
                    <span className="text-[9px] cyber-chip text-[color:var(--color-cyber-accent)]">
                      chargé
                    </span>
                  )}
                </div>
                <div className="text-[10px] text-[color:var(--color-cyber-muted)] mt-1 flex items-center gap-1.5 flex-wrap">
                  <Clock className="h-3 w-3 shrink-0" />
                  <span>
                    {new Date(row.started_at).toLocaleString("fr-FR", {
                      dateStyle: "short",
                      timeStyle: "short",
                    })}
                  </span>
                  {row.lat != null && row.lon != null && (
                    <>
                      <span className="text-[color:var(--color-cyber-border)]">·</span>
                      <MapPin className="h-3 w-3 shrink-0" />
                      <span className="font-mono">
                        {row.lat.toFixed(3)}, {row.lon.toFixed(3)}
                      </span>
                      <span className="cyber-chip text-[9px] uppercase">
                        {row.source}
                      </span>
                    </>
                  )}
                </div>
              </button>
              <button
                onClick={() => {
                  if (confirm(`Supprimer le scan #${row.id} ?`)) {
                    delMut.mutate(row.id);
                  }
                }}
                disabled={delMut.isPending}
                className="shrink-0 p-1 text-[color:var(--color-cyber-muted)] hover:text-amber-300"
                title="Supprimer ce scan"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
          </div>
        </>,
        document.body,
      )}
    </div>
  );
}

/** Duration options for the scan selector.
 *  Single-pass (~3-25s depending on band) is the default ("standard").
 *  Multi-pass loops `iw scan` until the budget is spent and merges by
 *  BSSID — useful to catch rarely-beaconing APs or to time-average RSSI. */
const SCAN_DURATIONS: ReadonlyArray<{
  value: number;
  label: string;
  hint: string;
}> = [
  { value: 0, label: "standard", hint: "1 passe (~3-25s)" },
  { value: 120, label: "2 min", hint: "scan approfondi" },
  { value: 300, label: "5 min", hint: "patrouille — capte les APs rares" },
  { value: 600, label: "10 min", hint: "surveillance courte — RSSI moyenné" },
];

function ScanDurationSelect({
  value,
  onChange,
  disabled,
}: {
  value: number;
  onChange: (v: number) => void;
  disabled: boolean;
}) {
  const current = SCAN_DURATIONS.find((d) => d.value === value);
  return (
    <select
      value={String(value)}
      onChange={(e) => onChange(Number(e.target.value))}
      disabled={disabled}
      className="cyber-input text-[10px] py-1 px-2"
      title={current ? current.hint : "Durée du scan"}
    >
      {SCAN_DURATIONS.map((d) => (
        <option key={d.value} value={d.value}>
          {d.label}
        </option>
      ))}
    </select>
  );
}

/** Live countdown shown inside the START SCAN button while scanning.
 *  For single-pass we just show a spinner ; for multi-pass we count
 *  down from the chosen budget so the operator sees actual progress. */
function ScanningButtonContent({ durationS }: { durationS: number }) {
  const [elapsedS, setElapsedS] = useState(0);
  useEffect(() => {
    if (durationS <= 0) return;
    const t0 = Date.now();
    const id = window.setInterval(() => {
      setElapsedS(Math.floor((Date.now() - t0) / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, [durationS]);
  if (durationS <= 0) {
    return (
      <>
        <RefreshCw className="mr-2 inline h-3 w-3 animate-spin" />
        scan en cours…
      </>
    );
  }
  const remaining = Math.max(0, durationS - elapsedS);
  const mm = Math.floor(remaining / 60);
  const ss = String(remaining % 60).padStart(2, "0");
  return (
    <>
      <RefreshCw className="mr-2 inline h-3 w-3 animate-spin" />
      scan… {mm}:{ss}
    </>
  );
}

/** Badge "vu N×" + range RSSI quand seen_count > 1 (donc multi-pass). */
function VAPSeenBadge({ n }: { n: NeighborAPView }) {
  const seen = n.seen_count ?? 1;
  if (seen <= 1) return null;
  const max = n.rssi_max ?? n.rssi_dbm;
  const min = n.rssi_min ?? n.rssi_dbm;
  const drift = max - min;
  return (
    <span
      className="inline-flex items-center gap-1 font-mono text-[9px] px-1 py-0.5 rounded"
      style={{
        color: "var(--color-cyber-muted)",
        border: "1px dashed var(--color-cyber-border)",
      }}
      title={`Vu ${seen}× pendant le scan · RSSI ${min}…${max} dBm (drift ${drift} dB)`}
    >
      ×{seen}
      {drift > 8 && (
        <span
          className="text-amber-300"
          title="RSSI varie > 8 dB — probablement device mobile"
        >
          ~
        </span>
      )}
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
