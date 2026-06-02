import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  CheckCircle2,
  Download,
  Globe,
  Loader2,
  Plus,
  Power,
  RefreshCw,
  Shield,
  Trash2,
  XCircle,
} from "lucide-react";

import {
  createTorBridge,
  deleteTorBridge,
  getTorSettings,
  getTorStatus,
  installTor,
  listTorBridges,
  updateTorSettings,
} from "@/api/tor";
import type { TorBridgeKind, TorSettingsWrite } from "@/types/tor";
import { EXIT_COUNTRY_PICKS, flagFor } from "@/lib/country-coords";
import { errorMessage } from "@/lib/error-utils";

/**
 * Global Tor section for the top of the Networks page.
 *
 * Three responsibilities :
 *   - Status snapshot (installed / daemon up / bootstrap %) — polled every
 *     8 s, cheap on the device side.
 *   - Toggle the daemon master switch + use_bridges.
 *   - Bridge list (paste / enable / delete obfs4 lines).
 *
 * Per-network routing toggles live inside the NetworkForm — this card is
 * intentionally cross-cutting only.
 */
export default function TorStatusCard() {
  const qc = useQueryClient();

  const status = useQuery({
    queryKey: ["tor", "status"],
    queryFn: getTorStatus,
    refetchInterval: 8_000,
  });
  const settings = useQuery({
    queryKey: ["tor", "settings"],
    queryFn: getTorSettings,
  });
  const bridges = useQuery({
    queryKey: ["tor", "bridges"],
    queryFn: listTorBridges,
  });

  const settingsMut = useMutation({
    mutationFn: updateTorSettings,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tor", "settings"] });
    },
  });

  const installMut = useMutation({
    mutationFn: installTor,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tor", "status"] });
    },
  });

  const createBridgeMut = useMutation({
    mutationFn: createTorBridge,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tor", "bridges"] });
    },
  });

  const deleteBridgeMut = useMutation({
    mutationFn: deleteTorBridge,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tor", "bridges"] });
    },
  });

  const [bridgeKind, setBridgeKind] = useState<TorBridgeKind>("obfs4");
  const [bridgeLine, setBridgeLine] = useState("");
  const [bridgeNote, setBridgeNote] = useState("");

  function patchSettings(patch: Partial<TorSettingsWrite>) {
    const base: TorSettingsWrite = {
      daemon_enabled: settings.data?.daemon_enabled ?? false,
      use_bridges: settings.data?.use_bridges ?? false,
      exit_country_code: settings.data?.exit_country_code ?? "",
    };
    settingsMut.mutate({ ...base, ...patch });
  }

  function submitBridge(e: FormEvent) {
    e.preventDefault();
    const line = bridgeLine.trim();
    if (!line) return;
    createBridgeMut.mutate(
      {
        kind: bridgeKind,
        bridge_line: line,
        note: bridgeNote.trim(),
        enabled: true,
      },
      {
        onSuccess: () => {
          setBridgeLine("");
          setBridgeNote("");
        },
      },
    );
  }

  const torInstalled = status.data?.install.tor ?? false;
  const obfsInstalled = status.data?.install.obfs4proxy ?? false;
  const daemonRunning = status.data?.daemon_running ?? false;
  const bootstrap = status.data?.bootstrap_progress;
  const phase = status.data?.bootstrap_phase ?? null;

  return (
    <section className="cyber-panel mt-6 mb-4 border border-purple-500/40 bg-purple-950/10 p-5">
      <header className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-purple-300" />
          <h2 className="cyber-heading text-base text-purple-200">
            Tor — anonymisation globale
          </h2>
          <button
            type="button"
            onClick={() => status.refetch()}
            className="cyber-chip-ghost ml-2 p-1"
            title="Rafraîchir le statut"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>
        <span className="text-xs text-zinc-400">
          Le routage <strong>per-réseau</strong> se règle sur chaque ligne plus
          bas.
        </span>
      </header>

      {/* Coexistence note with GL.iNet's stock Tor UI page (gl-sdk4-ui-
          torview). We lock their toggle out via uci tor.global.manual=1
          at every apply, but the user should know not to touch it. */}
      <div className="mb-3 flex items-start gap-2 rounded border border-yellow-500/30 bg-yellow-950/10 px-2.5 py-1.5 text-[11px] text-yellow-200/90">
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          Le menu Tor de l'UI GL.iNet est <strong>désactivé automatiquement</strong>{" "}
          (<code>uci tor.global.manual=1</code>) à chaque apply de profil — ne
          l'utilise pas, il écraserait notre <code>/etc/tor/torrc</code>. Le
          torrc d'usine est sauvegardé en <code>/etc/tor/torrc.gl-orig</code>.
        </span>
      </div>

      {/* ── Status grid ─────────────────────────────────────────────── */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatusTile
          label="tor (daemon)"
          ok={torInstalled}
          text={torInstalled ? "installé" : "absent"}
        />
        <StatusTile
          label="obfs4proxy (bridges)"
          ok={obfsInstalled}
          text={obfsInstalled ? "installé" : "absent"}
        />
        <StatusTile
          label="Daemon"
          ok={daemonRunning}
          text={daemonRunning ? "en cours" : "arrêté"}
        />
        <StatusTile
          label="Bootstrap"
          ok={bootstrap === 100}
          text={
            bootstrap === null || bootstrap === undefined
              ? "—"
              : `${bootstrap}%${phase ? ` (${phase})` : ""}`
          }
        />
      </div>

      {/* ── Install button (only if anything missing) ───────────────── */}
      {(!torInstalled || !obfsInstalled) && (
        <div className="mb-4 cyber-chip cyber-chip-warn block !rounded-none px-3 py-2 text-xs">
          <div className="flex items-center justify-between gap-3">
            <span>
              Packages manquants : install via opkg (peut prendre 30–90 s).
            </span>
            <button
              type="button"
              onClick={() => installMut.mutate()}
              disabled={installMut.isPending}
              className="cyber-btn cyber-btn-primary flex items-center gap-2"
            >
              {installMut.isPending ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Installation…
                </>
              ) : (
                <>
                  <Download className="h-3.5 w-3.5" /> Installer
                </>
              )}
            </button>
          </div>
          {installMut.error && (
            <p className="mt-1 text-red-300">
              {errorMessage(installMut.error)}
            </p>
          )}
        </div>
      )}

      {/* ── Global toggles ──────────────────────────────────────────── */}
      <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2">
        <ToggleRow
          label="Activer le daemon Tor"
          hint="Master switch global. Aucun réseau ne sera routé tant que c'est OFF."
          icon={<Power className="h-4 w-4" />}
          checked={settings.data?.daemon_enabled ?? false}
          onChange={(v) => patchSettings({ daemon_enabled: v })}
          disabled={!torInstalled || settingsMut.isPending}
        />
        <ToggleRow
          label="Utiliser des bridges (obfs4)"
          hint="Nécessaire en zone censurée (CN, IR, RU…). Demande obfs4proxy."
          icon={<Globe className="h-4 w-4" />}
          checked={settings.data?.use_bridges ?? false}
          onChange={(v) => patchSettings({ use_bridges: v })}
          disabled={!torInstalled || !obfsInstalled || settingsMut.isPending}
        />
      </div>

      {/* ── Exit country picker ──────────────────────────────────────── */}
      <div className="mb-4 rounded border border-zinc-700 bg-zinc-900/40 p-3">
        <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-purple-200">
          <Globe className="h-4 w-4" />
          Forcer le pays de sortie
        </div>
        <div className="mb-2 text-[10px] text-zinc-500">
          Quand actif, ajoute <code>ExitNodes {"{xx}"}</code> +{" "}
          <code>StrictNodes 1</code> à torrc. Les circuits qui ne peuvent
          pas satisfaire la contrainte échouent (au lieu de sortir
          ailleurs en silence).
        </div>
        <select
          value={settings.data?.exit_country_code ?? ""}
          onChange={(e) => patchSettings({ exit_country_code: e.target.value })}
          disabled={!torInstalled || settingsMut.isPending}
          className="cyber-input w-full text-xs"
        >
          <option value="">— pas de contrainte (Tor choisit librement) —</option>
          {EXIT_COUNTRY_PICKS.map((c) => (
            <option key={c.code} value={c.code}>
              {c.label}
            </option>
          ))}
        </select>
      </div>

      {/* ── Live exit IP / country pulled from the active circuit ─────── */}
      {daemonRunning && status.data?.exit_country && (
        <div className="mb-4 rounded border border-emerald-500/30 bg-emerald-950/10 p-2 text-xs">
          <div className="flex items-center gap-2">
            <span className="text-emerald-300">Sortie actuelle :</span>
            <span className="font-mono text-zinc-200">
              {flagFor(status.data.exit_country)}{" "}
              {status.data.exit_country.toUpperCase()}
              {status.data.exit_ip && (
                <span className="ml-2 text-zinc-400">
                  · {status.data.exit_ip}
                </span>
              )}
            </span>
            {status.data.bytes_read !== null && status.data.bytes_read !== undefined && (
              <span className="ml-auto text-[10px] text-zinc-500">
                ↓ {formatBytes(status.data.bytes_read)} ·{" "}
                ↑ {formatBytes(status.data.bytes_written ?? 0)}
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── Bridges list ───────────────────────────────────────────── */}
      <div className="mt-4">
        <h3 className="cyber-heading mb-2 text-sm text-purple-200">
          Bridges configurés ({bridges.data?.length ?? 0})
        </h3>
        {bridges.data && bridges.data.length > 0 ? (
          <ul className="mb-3 space-y-1.5">
            {bridges.data.map((b) => (
              <li
                key={b.id}
                className="flex items-start gap-2 rounded border border-zinc-700 bg-zinc-900/60 px-2 py-1.5 text-xs"
              >
                <span className="cyber-chip cyber-chip-ghost shrink-0 px-1.5 py-0.5">
                  {b.kind}
                </span>
                <code className="grow break-all text-zinc-300">
                  {b.bridge_line}
                </code>
                {b.note && (
                  <span className="text-zinc-500" title="Note">
                    — {b.note}
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => deleteBridgeMut.mutate(b.id)}
                  className="cyber-chip-ghost shrink-0 p-1 hover:text-red-300"
                  title="Supprimer ce bridge"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mb-3 text-xs text-zinc-500">
            Aucun bridge configuré. Récupère des bridges obfs4 sur{" "}
            <code>https://bridges.torproject.org</code> ou par email
            (bridges@torproject.org) puis colle-les ici.
          </p>
        )}
        <form
          onSubmit={submitBridge}
          className="grid grid-cols-1 gap-2 md:grid-cols-[110px_1fr_160px_auto]"
        >
          <select
            value={bridgeKind}
            onChange={(e) => setBridgeKind(e.target.value as TorBridgeKind)}
            className="cyber-input"
          >
            <option value="obfs4">obfs4</option>
            <option value="webtunnel">webtunnel</option>
            <option value="snowflake">snowflake</option>
            <option value="vanilla">vanilla</option>
          </select>
          <input
            type="text"
            placeholder="obfs4 1.2.3.4:443 FINGERPRINT cert=... iat-mode=0"
            value={bridgeLine}
            onChange={(e) => setBridgeLine(e.target.value)}
            className="cyber-input font-mono text-xs"
          />
          <input
            type="text"
            placeholder="Note (optionnel)"
            value={bridgeNote}
            onChange={(e) => setBridgeNote(e.target.value)}
            className="cyber-input"
          />
          <button
            type="submit"
            disabled={!bridgeLine.trim() || createBridgeMut.isPending}
            className="cyber-btn flex items-center gap-1"
          >
            <Plus className="h-3.5 w-3.5" /> Ajouter
          </button>
        </form>
        {createBridgeMut.error && (
          <p className="mt-1 text-xs text-red-300">
            {errorMessage(createBridgeMut.error)}
          </p>
        )}
      </div>
    </section>
  );
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`;
  return `${(b / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function StatusTile({
  label,
  ok,
  text,
}: {
  label: string;
  ok: boolean;
  text: string;
}) {
  const Icon = ok ? CheckCircle2 : XCircle;
  return (
    <div className="rounded border border-zinc-700 bg-zinc-900/40 p-2 text-xs">
      <div className="mb-0.5 text-zinc-500">{label}</div>
      <div
        className={`flex items-center gap-1.5 ${
          ok ? "text-emerald-300" : "text-zinc-400"
        }`}
      >
        <Icon className="h-3.5 w-3.5" />
        <span>{text}</span>
      </div>
    </div>
  );
}

function ToggleRow({
  label,
  hint,
  icon,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint?: string;
  icon?: React.ReactNode;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label
      className={`flex cursor-pointer items-start gap-3 rounded border p-2 transition ${
        disabled
          ? "cursor-not-allowed border-zinc-800 bg-zinc-900/30 opacity-50"
          : "border-zinc-700 bg-zinc-900/40 hover:border-purple-400/60"
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1"
      />
      <div className="text-xs">
        <div className="flex items-center gap-1.5 font-semibold text-purple-200">
          {icon}
          {label}
        </div>
        {hint && <div className="mt-0.5 text-zinc-500">{hint}</div>}
      </div>
      {!checked && !disabled && (
        <AlertCircle className="ml-auto h-3.5 w-3.5 shrink-0 self-center text-zinc-600" />
      )}
    </label>
  );
}
