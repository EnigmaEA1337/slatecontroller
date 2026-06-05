// PCAP capture page — LAN tcpdump on the Slate (Phase 1).
//
// Operator picks an iface + duration + (optional) BPF filter, hits Start.
// The backend kicks tcpdump as a background process ; we poll status
// every 2s while a capture is running. Past captures are listed below
// with size + download / delete actions.

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Download,
  PlayCircle,
  RefreshCw,
  Square,
  Trash2,
  Wifi,
} from "lucide-react";

import {
  type PcapCapture,
  type PcapStatus,
  deletePcapCapture,
  downloadPcapCapture,
  listPcapCaptures,
  startPcapCapture,
  stopPcapCapture,
} from "@/api/pcap";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";

const STATUS_META: Record<
  PcapStatus,
  { i18nKey: string; color: string; icon: typeof CheckCircle2 }
> = {
  planned: { i18nKey: "pcap.status_planned", color: "#64748b", icon: Clock },
  running: { i18nKey: "pcap.status_running", color: "#2563eb", icon: RefreshCw },
  completed: { i18nKey: "pcap.status_completed", color: "#047357", icon: CheckCircle2 },
  failed: { i18nKey: "pcap.status_failed", color: "#92400e", icon: AlertTriangle },
  cancelled: { i18nKey: "pcap.status_cancelled", color: "#64748b", icon: Square },
};

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export default function PcapPage() {
  const t = useT();
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["network", "pcap"],
    queryFn: () => listPcapCaptures(),
    // Faster polling when there's a running capture so the size bumps.
    refetchInterval: (q) => {
      const data = q.state.data;
      const running = data?.captures.some((c) => c.status === "running");
      return running ? 2000 : 10000;
    },
  });

  const [iface, setIface] = useState("br-lan");
  const [duration, setDuration] = useState(30);
  const [snaplen, setSnaplen] = useState(256);
  const [filterExpr, setFilterExpr] = useState("");
  const [label, setLabel] = useState("");

  // Sync defaults from server on first load (e.g. default_snaplen).
  useEffect(() => {
    if (!list.data) return;
    if (snaplen === 256 && list.data.limits.default_snaplen !== 256) {
      setSnaplen(list.data.limits.default_snaplen);
    }
  }, [list.data, snaplen]);

  const start = useMutation({
    mutationFn: () =>
      startPcapCapture({
        iface,
        duration_s: duration,
        snaplen,
        filter_expr: filterExpr,
        label,
      }),
    onSuccess: () => {
      setFilterExpr("");
      setLabel("");
      qc.invalidateQueries({ queryKey: ["network", "pcap"] });
    },
  });
  const stop = useMutation({
    mutationFn: (id: number) => stopPcapCapture(id),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["network", "pcap"] }),
  });
  const del = useMutation({
    mutationFn: (id: number) => deletePcapCapture(id),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["network", "pcap"] }),
  });

  const captures = list.data?.captures ?? [];
  const ifaces = list.data?.allowed_ifaces ?? [];
  const limits = list.data?.limits;

  return (
    <div className="space-y-4">
      <header className="cyber-label flex items-center gap-2">
        <Wifi className="h-3 w-3" /> {t("pcap.title")}
      </header>

      <p className="text-xs text-[color:var(--color-cyber-muted)] max-w-3xl">
        {t("pcap.description")}
      </p>

      {/* ── Start form ────────────────────────────────────────────── */}
      <section className="cyber-card p-4 space-y-3">
        <header className="cyber-label text-[10px] flex items-center gap-2">
          <PlayCircle className="h-3 w-3" /> {t("pcap.start")}
        </header>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              {t("pcap.form_iface")}
            </label>
            <select
              value={iface}
              onChange={(e) => setIface(e.target.value)}
              className="cyber-input w-full text-xs"
            >
              {ifaces.map((i) => (
                <option key={i} value={i}>
                  {i}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              {t("pcap.form_duration")}&nbsp;
              <span className="text-[color:var(--color-cyber-accent)] font-mono">
                {duration}s
              </span>
            </label>
            <input
              type="range"
              min={limits?.min_duration_s ?? 5}
              max={limits?.max_duration_s ?? 300}
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
              className="w-full"
            />
          </div>
          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              {t("pcap.form_snaplen")}&nbsp;
              <span className="text-[color:var(--color-cyber-accent)] font-mono">
                {snaplen} B
              </span>
            </label>
            <input
              type="range"
              min={limits?.min_snaplen ?? 64}
              max={limits?.max_snaplen ?? 65535}
              step={64}
              value={snaplen}
              onChange={(e) => setSnaplen(Number(e.target.value))}
              className="w-full"
            />
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              {t("pcap.form_filter")}
            </label>
            <input
              type="text"
              value={filterExpr}
              onChange={(e) => setFilterExpr(e.target.value)}
              placeholder={t("pcap.form_filter_placeholder")}
              maxLength={512}
              className="cyber-input w-full text-xs font-mono"
            />
          </div>
          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              {t("pcap.form_label")}
            </label>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder={t("pcap.form_label_placeholder")}
              maxLength={128}
              className="cyber-input w-full text-xs"
            />
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => start.mutate()}
            disabled={start.isPending}
            className="cyber-button px-4 py-2 text-xs"
          >
            {start.isPending ? (
              <>
                <RefreshCw className="mr-2 inline h-3 w-3 animate-spin" />
                {t("common.loading")}
              </>
            ) : (
              <>▶ {t("pcap.start")}</>
            )}
          </button>
          {start.error && (
            <span className="text-[11px] text-[color:var(--color-cyber-warn)]">
              ⚠ {errorMessage(start.error)}
            </span>
          )}
        </div>
      </section>

      {/* ── Captures table ────────────────────────────────────────── */}
      <section className="cyber-card p-3">
        <header className="cyber-label text-[10px] mb-2">
          {t("pcap.captures_title")} ({captures.length})
        </header>
        {captures.length === 0 ? (
          <p className="text-xs text-[color:var(--color-cyber-muted)]">
            {t("pcap.no_captures")}
          </p>
        ) : (
          <table className="cyber-table">
            <colgroup>
              <col className="w-12" />
              <col className="w-24" />
              <col />
              <col className="w-24" />
              <col className="w-40" />
              <col className="w-32" />
              <col className="w-24" />
              <col className="w-40" />
            </colgroup>
            <thead>
              <tr>
                <th>{t("pcap.col_id")}</th>
                <th>{t("pcap.col_iface")}</th>
                <th>{t("pcap.col_label")}</th>
                <th>{t("pcap.col_elapsed")}</th>
                <th>{t("pcap.col_filter")}</th>
                <th>{t("pcap.col_status")}</th>
                <th>{t("pcap.col_bytes")}</th>
                <th className="text-right">{t("pcap.col_actions")}</th>
              </tr>
            </thead>
            <tbody>
              {captures.map((c) => (
                <CaptureRow
                  key={c.id}
                  c={c}
                  onStop={() => stop.mutate(c.id)}
                  onDelete={() => {
                    if (confirm(`${t("common.delete")} #${c.id} ?`)) {
                      del.mutate(c.id);
                    }
                  }}
                  stopping={stop.isPending}
                  deleting={del.isPending}
                />
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function CaptureRow({
  c,
  onStop,
  onDelete,
  stopping,
  deleting,
}: {
  c: PcapCapture;
  onStop: () => void;
  onDelete: () => void;
  stopping: boolean;
  deleting: boolean;
}) {
  const t = useT();
  const meta = STATUS_META[c.status];
  const Icon = meta.icon;
  const elapsed = useMemo(() => {
    const start = new Date(c.started_at).getTime();
    const end = c.ended_at ? new Date(c.ended_at).getTime() : Date.now();
    return Math.max(0, Math.floor((end - start) / 1000));
  }, [c.started_at, c.ended_at, c.status]);

  // État local du téléchargement — affiche un spinner sur le bouton et
  // bloque les clics répétés tant que la requête tourne. Pas dans une
  // mutation tanstack-query parce que c'est purement transactionnel
  // (pas de cache à invalider après).
  const [downloading, setDownloading] = useState(false);
  const [dlError, setDlError] = useState<string | null>(null);
  const handleDownload = async () => {
    if (downloading) return;
    setDownloading(true);
    setDlError(null);
    try {
      const fname = `slate-pcap-${c.id}-${c.iface}.pcap`;
      await downloadPcapCapture(c.id, fname);
    } catch (e) {
      setDlError(errorMessage(e));
    } finally {
      setDownloading(false);
    }
  };
  return (
    <tr>
      <td>#{c.id}</td>
      <td>{c.iface}</td>
      <td className="text-[color:var(--color-cyber-muted)]">
        {c.label || t("common.none")}
      </td>
      <td>
        {elapsed}/{c.duration_s}s
      </td>
      <td className="text-[10px] text-[color:var(--color-cyber-muted)]">
        {c.filter_expr || t("common.none")}
      </td>
      <td>
        <span
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px]"
          style={{ color: meta.color, border: `1px solid ${meta.color}66` }}
        >
          <Icon
            className={cn(
              "h-3 w-3",
              c.status === "running" && "animate-spin",
            )}
          />
          {t(meta.i18nKey)}
        </span>
        {c.status === "failed" && c.error && (
          <div
            className="text-[9px] text-[color:var(--color-cyber-warn)] mt-0.5 truncate"
            title={c.error}
          >
            {c.error}
          </div>
        )}
      </td>
      <td>{formatBytes(c.bytes_captured)}</td>
      <td className="text-right">
        <div className="inline-flex items-center gap-1">
          {c.status === "running" && (
            <button
              onClick={onStop}
              disabled={stopping}
              className="cyber-button-ghost px-2 py-0.5 text-[10px]"
              title={t("pcap.action_stop")}
            >
              {t("pcap.action_stop")}
            </button>
          )}
          {(c.status === "completed" || c.status === "cancelled") &&
            c.bytes_captured > 0 && (
              <button
                onClick={handleDownload}
                disabled={downloading}
                className="cyber-button-ghost px-2 py-0.5 text-[10px] inline-flex items-center gap-1"
                title={
                  dlError
                    ? t("pcap.action_download_failed", { error: dlError })
                    : t("pcap.action_download_title")
                }
              >
                <Download
                  className={cn("h-3 w-3", downloading && "animate-spin")}
                />
                {downloading ? "…" : ".pcap"}
              </button>
            )}
          <button
            onClick={onDelete}
            disabled={deleting}
            className="cyber-button-ghost p-1 text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-warn)]"
            title={t("pcap.action_delete")}
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      </td>
    </tr>
  );
}
