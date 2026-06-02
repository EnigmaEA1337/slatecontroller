import { FormEvent, memo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Eraser,
  Eye,
  EyeOff,
  Fingerprint,
  Globe,
  Plus,
  Radio,
  RefreshCw,
  RotateCcw,
  Router,
  Shield,
  Star,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import {
  adoptDevice,
  createDevice,
  deleteDevice,
  forgetDevice,
  listDevices,
  probeDevice,
  setDefaultDevice,
} from "@/api/devices";
import { ClickableHost, ClickableHostList } from "@/components/ClickableHost";
import EditAdminUrlsModal from "@/components/EditAdminUrlsModal";
import FactoryResetModal from "@/components/FactoryResetModal";
import ScreenLockWidget from "@/components/ScreenLockWidget";
import type {
  AdoptionOptions,
  AdoptionTaskReport,
  DevicePublic,
} from "@/types/device";
import { cn } from "@/lib/utils";
import { errorMessage, formatDate } from "@/lib/error-utils";



// ---------------------------- Add device form ---------------------------- #

function AddDeviceForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [slug, setSlug] = useState("");
  const [label, setLabel] = useState("");
  const [host, setHost] = useState("");
  const [rpcUsername, setRpcUsername] = useState("root");
  const [rpcPassword, setRpcPassword] = useState("");
  const [showPw, setShowPw] = useState(false);

  const submit = useMutation({
    mutationFn: () =>
      createDevice({
        slug,
        label,
        host,
        rpc_username: rpcUsername,
        rpc_password: rpcPassword,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      onClose();
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit.mutate();
  }

  return (
    <form onSubmit={onSubmit} className="cyber-card cyber-card-accent space-y-4 p-5">
      <div className="flex items-center justify-between">
        <h3 className="cyber-display cyber-glow text-lg">NEW DEVICE</h3>
        <button
          type="button"
          onClick={onClose}
          className="border border-transparent p-1.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="cyber-label mb-1.5 block">slug</span>
          <input
            type="text"
            required
            value={slug}
            onChange={(e) =>
              setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))
            }
            placeholder="slate-mobile"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
        <label className="block">
          <span className="cyber-label mb-1.5 block">label (optionnel)</span>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Slate du sac à dos"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
        <label className="col-span-2 block">
          <span className="cyber-label mb-1.5 block">host (IP ou DNS)</span>
          <input
            type="text"
            required
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="192.168.8.1"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
        <label className="block">
          <span className="cyber-label mb-1.5 block">SSH/RPC user</span>
          <input
            type="text"
            required
            value={rpcUsername}
            onChange={(e) => setRpcUsername(e.target.value)}
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
        <label className="block">
          <span className="cyber-label mb-1.5 block">password</span>
          <div className="relative">
            <input
              type={showPw ? "text" : "password"}
              required
              value={rpcPassword}
              onChange={(e) => setRpcPassword(e.target.value)}
              className="cyber-input w-full py-2 px-3 pr-9 text-sm font-mono"
            />
            <button
              type="button"
              onClick={() => setShowPw((s) => !s)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-accent)]"
            >
              {showPw ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            </button>
          </div>
        </label>
      </div>

      <div className="flex gap-2 pt-2">
        <button
          type="submit"
          disabled={submit.isPending || !slug || !host || !rpcPassword}
          className="cyber-button px-4 py-2 text-xs disabled:opacity-50"
        >
          {submit.isPending ? "ajout…" : "Ajouter"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="border border-[color:var(--color-cyber-border-strong)] px-4 py-2 text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
        >
          Annuler
        </button>
      </div>

      {submit.error && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(submit.error)}
        </p>
      )}

      <p className="text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-dim)]">
        Après ajout : clique "adopter" → exécute TLS pinning, force HTTPS, SSH key-only, désactive UPnP.
      </p>
    </form>
  );
}

// ---------------------------- Adoption modal ---------------------------- #

function TaskRow({ task }: { task: AdoptionTaskReport }) {
  const Icon =
    task.status === "ok"
      ? CheckCircle2
      : task.status === "failed"
        ? XCircle
        : task.status === "skipped"
          ? CircleDashed
          : RefreshCw;
  const colorClass =
    task.status === "ok"
      ? "text-[color:var(--color-cyber-ok)]"
      : task.status === "failed"
        ? "text-[color:var(--color-cyber-accent)]"
        : "text-[color:var(--color-cyber-muted)]";
  return (
    <li className="flex items-start gap-2 border border-[color:var(--color-cyber-border)] p-2.5 text-[11px]">
      <Icon className={cn("mt-0.5 h-3 w-3 shrink-0", colorClass)} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[color:var(--color-cyber-fg)]">{task.name}</span>
          <span
            className={cn(
              "cyber-chip",
              task.status === "ok"
                ? "cyber-chip-ok"
                : task.status === "failed"
                  ? "cyber-chip-on"
                  : task.status === "skipped"
                    ? "cyber-chip-warn"
                    : "",
            )}
          >
            {task.status}
          </span>
        </div>
        {task.message && (
          <p className="mt-0.5 italic text-[color:var(--color-cyber-dim)]">{task.message}</p>
        )}
      </div>
    </li>
  );
}

function AdoptModal({
  device,
  onClose,
}: {
  device: DevicePublic;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [options, setOptions] = useState<AdoptionOptions>({
    pin_tls: true,
    force_https_webui: true,
    ssh_key_only: device.has_ssh_keypair && device.ssh_key_deployed,
    disable_upnp: true,
  });
  const run = useMutation({
    mutationFn: () => adoptDevice(device.slug, options),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      queryClient.invalidateQueries({ queryKey: ["slate-hardening"] });
    },
  });

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      {/* max-h-[90vh] + flex-col + overflow contained on the inner body
          so the modal stays inside the viewport even when the report
          balloons to 7+ tasks. Header + footer stay sticky-ish thanks
          to the flex shrink-0 + the scrollable middle. */}
      <div className="cyber-card cyber-card-accent flex max-h-[90vh] w-full max-w-2xl flex-col p-6">
        <header className="mb-4 flex shrink-0 items-center justify-between">
          <div>
            <h2 className="cyber-display cyber-glow text-lg">
              ADOPT · {device.slug}
            </h2>
            <p className="mt-0.5 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
              {device.host}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="border border-transparent p-1.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="cyber-hatch mb-4 h-px w-full shrink-0" />

        {/* Scrollable body — wraps both the form and the post-run report
            so a tall task list (7+ items, each with multiline messages)
            never pushes the close button off-screen. Negative right
            margin gives the scrollbar some breathing room. */}
        <div className="-mr-2 flex-1 overflow-y-auto pr-2">

        {!run.data && (
          <>
            <div className="space-y-2 text-xs">
              <label className="flex items-start gap-2 border border-[color:var(--color-cyber-border)] p-3 hover:border-[color:var(--color-cyber-border-strong)]">
                <input
                  type="checkbox"
                  checked={options.pin_tls}
                  onChange={(e) =>
                    setOptions((o) => ({ ...o, pin_tls: e.target.checked }))
                  }
                  className="mt-0.5 h-3.5 w-3.5"
                />
                <div>
                  <div className="font-bold uppercase tracking-[0.18em] text-[11px]">
                    TLS pinning
                  </div>
                  <div className="text-[10px] text-[color:var(--color-cyber-dim)]">
                    Fetch + stocke le SHA256 du cert auto-signé. Alerte si change ensuite (MITM/re-flash).
                  </div>
                </div>
              </label>

              <label className="flex items-start gap-2 border border-[color:var(--color-cyber-border)] p-3 hover:border-[color:var(--color-cyber-border-strong)]">
                <input
                  type="checkbox"
                  checked={options.force_https_webui}
                  onChange={(e) =>
                    setOptions((o) => ({ ...o, force_https_webui: e.target.checked }))
                  }
                  className="mt-0.5 h-3.5 w-3.5"
                />
                <div>
                  <div className="font-bold uppercase tracking-[0.18em] text-[11px]">
                    Force HTTPS web UI
                  </div>
                  <div className="text-[10px] text-[color:var(--color-cyber-dim)]">
                    <code className="font-mono">uci set uhttpd.main.redirect_https=1</code> · :80 → :443.
                  </div>
                </div>
              </label>

              <label
                className={cn(
                  "flex items-start gap-2 border p-3",
                  device.has_ssh_keypair && device.ssh_key_deployed
                    ? "border-[color:var(--color-cyber-border)] hover:border-[color:var(--color-cyber-border-strong)]"
                    : "border-[color:var(--color-cyber-warn)] bg-[color:var(--color-cyber-warn)]/8",
                )}
              >
                <input
                  type="checkbox"
                  checked={options.ssh_key_only}
                  onChange={(e) =>
                    setOptions((o) => ({ ...o, ssh_key_only: e.target.checked }))
                  }
                  disabled={!device.has_ssh_keypair || !device.ssh_key_deployed}
                  className="mt-0.5 h-3.5 w-3.5"
                />
                <div>
                  <div className="font-bold uppercase tracking-[0.18em] text-[11px]">
                    SSH key-only auth
                  </div>
                  <div className="text-[10px] text-[color:var(--color-cyber-dim)]">
                    Désactive dropbear PasswordAuth.
                    {!device.has_ssh_keypair || !device.ssh_key_deployed
                      ? " Génère + déploie d'abord la keypair via Settings → SSH keypair."
                      : ""}
                  </div>
                </div>
              </label>

              <label className="flex items-start gap-2 border border-[color:var(--color-cyber-border)] p-3 hover:border-[color:var(--color-cyber-border-strong)]">
                <input
                  type="checkbox"
                  checked={options.disable_upnp}
                  onChange={(e) =>
                    setOptions((o) => ({ ...o, disable_upnp: e.target.checked }))
                  }
                  className="mt-0.5 h-3.5 w-3.5"
                />
                <div>
                  <div className="font-bold uppercase tracking-[0.18em] text-[11px]">
                    Désactive UPnP
                  </div>
                  <div className="text-[10px] text-[color:var(--color-cyber-dim)]">
                    <code className="font-mono">uci set upnpd.config.enabled=0</code> · stop miniupnpd.
                  </div>
                </div>
              </label>
            </div>

            <div className="mt-4 flex gap-2">
              <button
                type="button"
                disabled={run.isPending}
                onClick={() => run.mutate()}
                className="cyber-button inline-flex items-center gap-2 px-4 py-2.5 text-xs disabled:opacity-50"
              >
                <Shield className="h-3.5 w-3.5" />
                {run.isPending ? "exécution…" : "Lancer l'adoption"}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="border border-[color:var(--color-cyber-border-strong)] px-4 py-2.5 text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
              >
                Annuler
              </button>
            </div>
            {run.error && (
              <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
                {errorMessage(run.error)}
              </p>
            )}
          </>
        )}

        {run.data && (
          <div className="space-y-3">
            <div
              className={cn(
                "border px-3 py-2 text-xs",
                run.data.overall_status === "ok"
                  ? "border-[color:var(--color-cyber-ok)] bg-[color:var(--color-cyber-ok)]/8 text-[color:var(--color-cyber-ok)]"
                  : run.data.overall_status === "partial"
                    ? "border-[color:var(--color-cyber-warn)] bg-[color:var(--color-cyber-warn)]/8 text-[color:var(--color-cyber-warn)]"
                    : "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8 text-[color:var(--color-cyber-accent)]",
              )}
            >
              {run.data.overall_status === "ok" && (
                <>
                  <CheckCircle2 className="mr-1.5 inline h-3 w-3" />
                  Adoption complète — toutes les tâches OK.
                </>
              )}
              {run.data.overall_status === "partial" && (
                <>
                  <AlertTriangle className="mr-1.5 inline h-3 w-3" />
                  Adoption partielle — certaines tâches ont échoué.
                </>
              )}
              {run.data.overall_status === "failed" && (
                <>
                  <XCircle className="mr-1.5 inline h-3 w-3" />
                  Adoption échouée.
                </>
              )}
            </div>
            <ul className="space-y-1.5">
              {run.data.tasks.map((t, i) => (
                <TaskRow key={i} task={t} />
              ))}
            </ul>
            <div className="flex gap-2 pt-2">
              <button
                type="button"
                onClick={onClose}
                className="cyber-button px-4 py-2 text-xs"
              >
                Fermer
              </button>
            </div>
          </div>
        )}

        </div>{/* /scrollable body */}
      </div>
    </div>
  );
}

// ---------------------------- Device card ---------------------------- #

// Memoised: rendered per-device. Parent re-renders happen on connectivity
// polling (every 15s), and we don't want all cards repainting just because
// one stat changed elsewhere. `onAdopt` receives the device via closure in
// the parent — using a useCallback with no deps because it just sets state.
const DeviceCard = memo(function DeviceCard({
  device,
  onAdopt,
}: {
  device: DevicePublic;
  onAdopt: (device: DevicePublic) => void;
}) {
  const queryClient = useQueryClient();
  const [editingUrls, setEditingUrls] = useState(false);
  const [factoryResetOpen, setFactoryResetOpen] = useState(false);
  const probe = useMutation({
    mutationFn: () => probeDevice(device.slug),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["devices"] }),
  });
  const setDefault = useMutation({
    mutationFn: () => setDefaultDevice(device.slug),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["devices"] }),
  });
  const remove = useMutation({
    mutationFn: () => deleteDevice(device.slug),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["devices"] }),
  });
  // Forget = reset adoption state locally (does NOT touch the Slate). Used
  // when the operator wants to re-run hardening from scratch but keep the
  // device's identity (host, credentials, TLS pin, SSH keypair).
  const forget = useMutation({
    mutationFn: () => forgetDevice(device.slug),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["devices"] }),
  });

  const isAdopted = device.status === "adopted";

  const statusChip =
    device.status === "adopted"
      ? "cyber-chip-ok"
      : device.status === "error"
        ? "cyber-chip-on"
        : "cyber-chip-warn";

  return (
    <article className="cyber-card p-5">
      <header className="flex items-start gap-3">
        <div
          className={cn(
            "flex h-10 w-10 shrink-0 items-center justify-center border",
            device.is_default
              ? "cyber-glow border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10"
              : "border-[color:var(--color-cyber-border)]",
          )}
        >
          <Router className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <h3 className="cyber-display cyber-glow text-base">{device.slug}</h3>
            <span className={cn("cyber-chip", statusChip)}>{device.status}</span>
            {device.is_default && (
              <span className="cyber-chip cyber-chip-ok inline-flex items-center gap-1">
                <Star className="h-2.5 w-2.5" />
                default
              </span>
            )}
            <span className="cyber-chip">{device.model}</span>
          </div>
          <p className="mt-1 text-xs text-[color:var(--color-cyber-muted)]">
            {device.label || "—"}
          </p>
          <div className="mt-2 grid grid-cols-1 gap-x-4 gap-y-0.5 text-[11px] sm:grid-cols-2">
            <span>
              host{" "}
              <span className="cyber-glow-soft font-mono">{device.host}</span>
            </span>
            <span className="sm:col-span-2">
              admin URLs{" "}
              <span className="cyber-glow-soft font-mono">
                {device.admin_urls.length > 0 ? (
                  <ClickableHostList
                    items={device.admin_urls}
                    separator=" / "
                  />
                ) : (
                  "(fallback host)"
                )}
              </span>
            </span>
            <span>
              rpc{" "}
              <span className="cyber-glow-soft font-mono">
                <ClickableHost
                  value={`${device.rpc_scheme}://${device.host}:${device.rpc_port}`}
                />
              </span>
            </span>
            <span>
              ssh{" "}
              <span className="cyber-glow-soft font-mono">
                {device.rpc_username}@{device.host}:{device.ssh_port}
              </span>
            </span>
            <span>
              dernier probe{" "}
              <span className="cyber-glow-soft font-mono">
                {formatDate(device.last_probe_at)}
              </span>
            </span>
            {device.adopted_at && (
              <span>
                adopté{" "}
                <span className="cyber-glow-soft font-mono">
                  {formatDate(device.adopted_at)}
                </span>
              </span>
            )}
          </div>
          {device.tls_fingerprint_sha256 && (
            <div className="mt-2">
              <div className="cyber-label mb-1 flex items-center gap-1.5">
                <Fingerprint className="h-3 w-3" />
                TLS pinned
              </div>
              <code className="block break-all rounded-none border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)] px-2 py-1 font-mono text-[10px]">
                {device.tls_fingerprint_sha256}
              </code>
            </div>
          )}
          {device.notes && (
            <p className="mt-2 text-[10px] italic text-[color:var(--color-cyber-dim)]">
              {device.notes}
            </p>
          )}
          {/* Touchscreen PIN lock — visible only on the default device since
              the controller's screen-lock endpoints target the singleton SSH. */}
          {isAdopted && <ScreenLockWidget isDefault={device.is_default} />}
        </div>
      </header>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={probe.isPending}
          onClick={() => probe.mutate()}
          className="inline-flex items-center gap-1.5 border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-50"
        >
          <Radio
            className={cn("h-3 w-3", probe.isPending && "animate-pulse")}
          />
          {probe.isPending ? "probe…" : "probe"}
        </button>

        <button
          type="button"
          onClick={() => setEditingUrls(true)}
          className="inline-flex items-center gap-1.5 border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          title="Éditer la liste des URLs admin (LAN, Tailscale, custom)"
        >
          <Globe className="h-3 w-3" />
          urls
        </button>

        {/* Action principale dépend du status :
            - pending/error → "Adopter" (run hardening from scratch)
            - adopted       → "Ré-adopter" (re-run hardening, idempotent) */}
        <button
          type="button"
          onClick={() => onAdopt(device)}
          className="cyber-button inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px]"
          title={
            isAdopted
              ? "Re-lance les hardening tasks (idempotent — peut être ré-exécuté sans risque)"
              : "Lance les hardening tasks pour la première fois"
          }
        >
          {isAdopted ? <RotateCcw className="h-3 w-3" /> : <Shield className="h-3 w-3" />}
          {isAdopted ? "ré-adopter" : "adopter"}
        </button>

        {/* Forget = reset local DB seulement, ne touche pas au Slate.
            Disponible si le device est déjà adopté ou en erreur. */}
        {isAdopted && (
          <button
            type="button"
            disabled={forget.isPending}
            onClick={() => {
              if (
                confirm(
                  `Oublier l'adoption de "${device.slug}" ?\n\nLe Slate n'est PAS touché — seul l'état local du contrôleur est réinitialisé (status → pending). Tu pourras ré-adopter ensuite.`,
                )
              )
                forget.mutate();
            }}
            className="inline-flex items-center gap-1.5 border border-transparent px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-warn)] hover:text-[color:var(--color-cyber-warn)] disabled:opacity-50"
            title="Réinitialise le status local en pending. Le Slate garde sa config."
          >
            <Eraser className="h-3 w-3" />
            oublier
          </button>
        )}

        {/* Factory reset : action DESTRUCTIVE qui wipe le Slate. Seulement
            visible quand adopté (sinon pas de SSH key déployée pour le faire). */}
        {isAdopted && (
          <button
            type="button"
            onClick={() => setFactoryResetOpen(true)}
            className="inline-flex items-center gap-1.5 border border-transparent px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
            title="DESTRUCTIVE : firstboot + reboot sur le Slate"
          >
            <AlertOctagon className="h-3 w-3" />
            factory reset
          </button>
        )}

        {!device.is_default && (
          <button
            type="button"
            disabled={setDefault.isPending}
            onClick={() => setDefault.mutate()}
            className="inline-flex items-center gap-1.5 border border-transparent px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            <Star className="h-3 w-3" />
            défaut
          </button>
        )}

        {!device.is_default && (
          <button
            type="button"
            disabled={remove.isPending}
            onClick={() => {
              if (confirm(`Supprimer le device "${device.slug}" ?`)) remove.mutate();
            }}
            className="ml-auto inline-flex items-center gap-1.5 border border-transparent px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-50"
          >
            <Trash2 className="h-3 w-3" />
            supprimer
          </button>
        )}
      </div>

      {probe.error && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(probe.error)}
        </p>
      )}
      {setDefault.error && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(setDefault.error)}
        </p>
      )}
      {remove.error && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(remove.error)}
        </p>
      )}
      {setDefault.data && !setDefault.error && (
        <p className="mt-3 border border-[color:var(--color-cyber-warn)] bg-[color:var(--color-cyber-warn)]/8 px-3 py-2 text-[11px]">
          <AlertTriangle className="mr-1.5 inline h-3 w-3" />
          Le device est marqué comme défaut. Restart le backend pour rebind les connexions.
        </p>
      )}
      {editingUrls && (
        <EditAdminUrlsModal device={device} onClose={() => setEditingUrls(false)} />
      )}
      {factoryResetOpen && (
        <FactoryResetModal
          deviceSlug={device.slug}
          deviceLabel={device.label || ""}
          onClose={() => setFactoryResetOpen(false)}
          onDone={() => {
            setFactoryResetOpen(false);
            queryClient.invalidateQueries({ queryKey: ["devices"] });
          }}
        />
      )}
      {forget.error && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(forget.error)}
        </p>
      )}
    </article>
  );
});

// ---------------------------- Page ---------------------------- #

export default function DevicesPage() {
  const [creating, setCreating] = useState(false);
  const [adoptingDevice, setAdoptingDevice] = useState<DevicePublic | null>(null);
  const query = useQuery({
    queryKey: ["devices"],
    queryFn: listDevices,
  });

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="cyber-label mb-2 flex items-center gap-2">
            <Router className="cyber-glow h-3 w-3" />
            managed devices · {query.data?.length ?? 0}
          </div>
          <h1 className="cyber-display cyber-glitch text-4xl" data-text="DEVICES">
            DEVICES
          </h1>
          <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
            GL.iNet hardware piloté par le contrôleur · adoption + hardening
          </p>
        </div>
        {!creating && (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="cyber-button inline-flex items-center gap-2 px-4 py-2.5 text-xs"
          >
            <Plus className="h-3.5 w-3.5" />
            Nouveau device
          </button>
        )}
      </header>

      {creating && (
        <section className="mb-6">
          <AddDeviceForm onClose={() => setCreating(false)} />
        </section>
      )}

      {query.isLoading && <p className="cyber-label cyber-cursor">chargement</p>}
      {query.error && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(query.error)}
        </p>
      )}
      {query.data && query.data.length === 0 && !creating && (
        <p className="text-xs text-[color:var(--color-cyber-muted)]">
          Aucun device. Clique "Nouveau device" pour démarrer.
        </p>
      )}

      {query.data && query.data.length > 0 && (
        <div className="space-y-4">
          {query.data.map((d) => (
            <DeviceCard key={d.slug} device={d} onAdopt={setAdoptingDevice} />
          ))}
        </div>
      )}

      {adoptingDevice && (
        <AdoptModal
          device={adoptingDevice}
          onClose={() => setAdoptingDevice(null)}
        />
      )}
    </div>
  );
}
