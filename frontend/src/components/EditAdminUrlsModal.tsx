import { useEffect, useId, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { AxiosError } from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Globe,
  Plus,
  RefreshCw,
  Trash2,
  X,
  XCircle,
} from "lucide-react";

import {
  type ConnectivityStatus,
  type DevicePublic,
  getSlateConnectivity,
  patchDevice,
} from "@/api/devices";
import { useModalA11y } from "@/hooks/useModalA11y";

/**
 * Edits the device's admin URL list (LAN + Tailscale + custom).
 *
 * Why this lives on the device card rather than a global setting:
 *   - Each device has its own paths (Slate via LAN/Tailscale, Mudi via 4G,
 *     etc.). Bundling them per-device avoids juggling N pairs of env vars.
 *   - The controller's URL resolver consumes `admin_urls` directly from
 *     the DB — no .env edits, no backend restart required.
 *
 * For the default device, the modal also displays the live connectivity
 * (which URL is currently active + latency on each candidate) — refreshed
 * automatically every 5s while the modal is open, or via a manual button.
 */
export default function EditAdminUrlsModal({
  device,
  onClose,
}: {
  device: DevicePublic;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [urls, setUrls] = useState<string[]>(
    device.admin_urls.length ? [...device.admin_urls] : [""],
  );

  // Live connectivity polling — only for the default device (the resolver
  // only tracks one device at a time in V1).
  const connectivity = useQuery({
    queryKey: ["slate", "connectivity"],
    queryFn: () => getSlateConnectivity(false),
    enabled: device.is_default,
    refetchInterval: 5000,
  });

  const refreshMut = useMutation({
    mutationFn: () => getSlateConnectivity(true),
    onSuccess: (data) =>
      qc.setQueryData(["slate", "connectivity"], data as ConnectivityStatus),
  });

  const saveMut = useMutation({
    mutationFn: () =>
      patchDevice(device.slug, {
        admin_urls: urls.map((u) => u.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices"] });
      qc.invalidateQueries({ queryKey: ["slate", "connectivity"] });
      onClose();
    },
  });

  // Keep the local state in sync if the prop changes (rare but possible).
  useEffect(() => {
    setUrls(device.admin_urls.length ? [...device.admin_urls] : [""]);
  }, [device.admin_urls]);

  const liveCandidates = useMemo(() => {
    const map = new Map<string, { reachable: boolean; latency: number | null }>();
    for (const c of connectivity.data?.candidates ?? []) {
      map.set(c.url.replace(/\/$/, ""), {
        reachable: c.reachable,
        latency: c.latency_ms,
      });
    }
    return map;
  }, [connectivity.data]);

  function setUrl(idx: number, value: string) {
    setUrls((prev) => prev.map((u, i) => (i === idx ? value : u)));
  }

  function addUrl() {
    setUrls((prev) => [...prev, ""]);
  }

  function removeUrl(idx: number) {
    setUrls((prev) => prev.filter((_, i) => i !== idx));
  }

  function moveUp(idx: number) {
    if (idx === 0) return;
    setUrls((prev) => {
      const next = [...prev];
      [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
      return next;
    });
  }

  const nonEmpty = urls.map((u) => u.trim()).filter(Boolean);
  const canSave = nonEmpty.length >= 1 && !saveMut.isPending;
  const titleId = useId();
  const panelRef = useModalA11y<HTMLDivElement>(onClose);

  // Portal to document.body: avoids the de-centering caused by ancestor
  // CSS (`body { background-attachment: fixed }` traps position:fixed
  // children in some browsers). See ThreatModelModal for the same fix.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="w-full max-w-2xl rounded-lg border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] p-6 shadow-2xl focus:outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 id={titleId} className="flex items-center gap-2 text-lg font-bold text-[color:var(--color-cyber-fg)]">
              <Globe className="h-5 w-5 text-cyan-400" />
              URLs admin — {device.label || device.slug}
            </h2>
            <p className="mt-1 text-xs text-[color:var(--color-cyber-muted)]">
              Liste ordonnée des URLs où le contrôleur joint ce device.
              Le première URL accessible est utilisée. Bascule automatique
              si une URL tombe.
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1.5 text-[color:var(--color-cyber-muted)] hover:bg-[color:var(--color-cyber-surface-2)] hover:text-[color:var(--color-cyber-fg)]"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <label className="mb-2 block text-sm font-medium text-[color:var(--color-cyber-fg)]">
          Candidates (par ordre de priorité)
        </label>
        <div className="mb-4 space-y-2">
          {urls.map((url, idx) => {
            const live = liveCandidates.get(url.trim().replace(/\/$/, ""));
            return (
              <div key={idx} className="flex items-center gap-2">
                <span className="w-6 text-center text-xs text-[color:var(--color-cyber-dim)]">
                  {idx + 1}.
                </span>
                <input
                  type="text"
                  value={url}
                  onChange={(e) => setUrl(idx, e.target.value)}
                  placeholder={
                    idx === 0
                      ? "https://192.168.8.1 (LAN)"
                      : "https://100.x.x.x (Tailscale)"
                  }
                  className="flex-1 rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-2 py-1.5 font-mono text-sm text-[color:var(--color-cyber-fg)]"
                />
                {live !== undefined && (
                  <span
                    className={`flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono ${
                      live.reachable
                        ? "bg-emerald-500/20 text-emerald-300"
                        : "bg-amber-500/20 text-amber-300"
                    }`}
                    title={
                      live.reachable
                        ? `Joignable, ${live.latency} ms`
                        : "Pas de réponse"
                    }
                  >
                    {live.reachable ? (
                      <>
                        <CheckCircle2 className="h-3 w-3" />
                        {live.latency} ms
                      </>
                    ) : (
                      <>
                        <XCircle className="h-3 w-3" />
                        OFF
                      </>
                    )}
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => moveUp(idx)}
                  disabled={idx === 0}
                  className="rounded border border-[color:var(--color-cyber-border-strong)] px-1.5 py-0.5 text-[10px] text-[color:var(--color-cyber-muted)] hover:border-cyan-500 hover:text-cyan-300 disabled:opacity-30"
                  title="Monter (priorité plus haute)"
                >
                  ↑
                </button>
                <button
                  type="button"
                  onClick={() => removeUrl(idx)}
                  disabled={urls.length <= 1}
                  className="rounded border border-[color:var(--color-cyber-border-strong)] px-1.5 py-1 text-[color:var(--color-cyber-muted)] hover:border-red-500 hover:text-red-300 disabled:opacity-30"
                  title="Supprimer cette URL"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            );
          })}
        </div>

        <div className="mb-4 flex items-center justify-between">
          <button
            type="button"
            onClick={addUrl}
            className="flex items-center gap-1.5 rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-2 py-1 text-xs text-[color:var(--color-cyber-fg)] hover:border-cyan-500 hover:text-cyan-300"
          >
            <Plus className="h-3 w-3" />
            Ajouter une URL
          </button>
          {device.is_default && (
            <button
              type="button"
              onClick={() => refreshMut.mutate()}
              disabled={refreshMut.isPending}
              className="flex items-center gap-1.5 rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-2 py-1 text-xs text-[color:var(--color-cyber-fg)] hover:border-cyan-500 hover:text-cyan-300 disabled:opacity-50"
            >
              <RefreshCw
                className={`h-3 w-3 ${refreshMut.isPending ? "animate-spin" : ""}`}
              />
              Re-sonder
            </button>
          )}
        </div>

        {device.is_default && connectivity.data && (
          <div className="mb-4 rounded border border-cyan-500/30 bg-cyan-500/5 p-3 text-xs">
            <p className="mb-1 font-medium text-cyan-300">
              URL active actuelle :
            </p>
            <code className="font-mono text-[color:var(--color-cyber-fg)]">
              {connectivity.data.active_url}
            </code>
          </div>
        )}

        {!device.is_default && (
          <div className="mb-4 rounded border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200">
            <AlertTriangle className="mr-1 inline h-3 w-3" />
            Ce device n'est pas le device par défaut. La bascule automatique
            ne s'applique qu'au device par défaut en V1.
          </div>
        )}

        {saveMut.isError && (
          <div className="mb-3 flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>
              {(saveMut.error as AxiosError<{ detail: string }>)?.response?.data
                ?.detail ?? (saveMut.error as Error)?.message}
            </span>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-4 py-2 text-sm text-[color:var(--color-cyber-fg)] hover:bg-slate-700"
          >
            Annuler
          </button>
          <button
            onClick={() => saveMut.mutate()}
            disabled={!canSave}
            className="rounded border border-cyan-500/40 bg-cyan-500/20 px-4 py-2 text-sm text-cyan-200 hover:bg-cyan-500/30 disabled:opacity-50"
          >
            {saveMut.isPending ? "Application…" : "Enregistrer"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
