import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  Eye,
  EyeOff,
  Hammer,
  Key,
  Lock,
  Pencil,
  Plus,
  Power,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { createPortal } from "react-dom";
import {
  buildFortinetBinary,
  connectFortinet,
  createFortinetConfig,
  deleteFortinetConfig,
  disconnectFortinet,
  getFortinetArtifact,
  getFortinetPreflight,
  getFortinetStatus,
  listFortinetConfigs,
  patchFortinetConfig,
  reconcileFortinetRouting,
  sideloadFortinetBinary,
} from "@/api/fortinet";
import type {
  FortinetConfigPublic,
  FortinetState,
} from "@/types/fortinet";
import { cn } from "@/lib/utils";
import { errorMessage, formatDate } from "@/lib/error-utils";

// ────────────────────────────────── helpers ──────────────────────────

const STATE_CHIP: Record<FortinetState, string> = {
  unknown: "",
  connecting: "cyber-chip-warn",
  up: "cyber-chip-ok",
  disconnecting: "cyber-chip-warn",
  down: "",
  failed: "cyber-chip-on",
};

const STATE_LABEL: Record<FortinetState, string> = {
  unknown: "inconnu",
  connecting: "connexion…",
  up: "UP",
  disconnecting: "déconnexion…",
  down: "DOWN",
  failed: "échec",
};

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtUptime(s: number): string {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), sec = s % 60;
  if (m < 60) return `${m}m${sec}s`;
  const h = Math.floor(m / 60), mm = m % 60;
  return `${h}h${mm}m`;
}

// ────────────────────────────────── preflight ────────────────────────

function PreflightBanner() {
  const queryClient = useQueryClient();
  const [buildLogs, setBuildLogs] = useState<string>("");

  const pfQ = useQuery({
    queryKey: ["forti", "preflight"],
    queryFn: getFortinetPreflight,
    staleTime: 60_000,
  });
  const artifactQ = useQuery({
    queryKey: ["forti", "artifact"],
    queryFn: getFortinetArtifact,
    staleTime: 30_000,
  });
  const build = useMutation({
    mutationFn: () => buildFortinetBinary("v1.21.0"),
    onSuccess: (r) => {
      setBuildLogs(r.logs);
      queryClient.invalidateQueries({ queryKey: ["forti", "artifact"] });
    },
  });
  const sideload = useMutation({
    mutationFn: sideloadFortinetBinary,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["forti"] });
    },
  });

  if (pfQ.isLoading) return null;
  const pf = pfQ.data;
  if (!pf) return null;

  // Happy path : binary present + preflight ok.
  if (pf.ok && !pf.error) {
    return (
      <div className="cyber-chip cyber-chip-ok inline-flex items-center gap-1.5 px-3 py-1.5 text-[10px]">
        <CheckCircle2 className="h-3 w-3" />
        openfortivpn prêt {pf.version && `· ${pf.version}`}
      </div>
    );
  }

  const art = artifactQ.data;
  const hasArtifact = !!art?.available;

  return (
    <div className="border border-[color:var(--color-cyber-warn)] bg-[color:var(--color-cyber-warn)]/8 p-4">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[color:var(--color-cyber-warn)]" />
        <div className="min-w-0 flex-1">
          <p className="text-xs text-[color:var(--color-cyber-warn)]">
            {pf.ok ? "Préflight avertissement" : "openfortivpn manquant sur le Slate"}
          </p>
          <p className="mt-1 text-[11px] text-[color:var(--color-cyber-muted)]">
            {pf.error}
          </p>
          {pf.binary && pf.binary !== "MISSING" && (
            <p className="mt-1 text-[10px] font-mono text-[color:var(--color-cyber-dim)]">
              binary actuel : {pf.binary} {pf.version && `(${pf.version})`}
            </p>
          )}

          {/* Build / sideload action row — appears only when the binary
              is missing on the Slate. The artifact endpoint tells us
              whether a fresh build already exists in the shared volume,
              so we can skip straight to Sideload on re-runs. */}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={build.isPending}
              onClick={() => build.mutate()}
              className="cyber-button inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px] disabled:opacity-50"
              title="Lance le container slate-forti-builder pour cross-compiler le binaire (~3-12 min)"
            >
              <Hammer
                className={cn("h-3.5 w-3.5", build.isPending && "animate-pulse")}
              />
              {build.isPending ? "build en cours…" : hasArtifact ? "Rebuild" : "Build"}
            </button>
            <button
              type="button"
              disabled={!hasArtifact || sideload.isPending}
              onClick={() => sideload.mutate()}
              className="inline-flex items-center gap-1.5 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8 px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/15 disabled:opacity-40"
              title={
                hasArtifact
                  ? "SCP le binaire vers /usr/sbin/openfortivpn sur le Slate + chmod 755"
                  : "Build d'abord pour produire le binaire"
              }
            >
              <Upload className="h-3.5 w-3.5" />
              {sideload.isPending ? "push…" : "Sideload"}
            </button>
            {art?.available && (
              <span className="text-[10px] text-[color:var(--color-cyber-muted)]">
                ▸ artifact : {art.version || art.git_ref} (
                {Math.round(art.size_bytes / 1024)} KB)
              </span>
            )}
          </div>

          {build.error && (
            <p className="mt-2 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-[10px]">
              {errorMessage(build.error)}
            </p>
          )}
          {sideload.error && (
            <p className="mt-2 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-[10px]">
              {errorMessage(sideload.error)}
            </p>
          )}
          {sideload.data && (
            <p className="mt-2 cyber-chip cyber-chip-ok block !rounded-none px-3 py-2 text-[10px]">
              ▸ poussé vers {sideload.data.remote_path} (
              {Math.round(sideload.data.size_bytes / 1024)} KB · sha
              {sideload.data.sha256.slice(0, 12)})
            </p>
          )}
          {buildLogs && (
            <details className="mt-2">
              <summary className="cursor-pointer text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]">
                <Download className="mr-1 inline h-2.5 w-2.5" />
                logs de build ({buildLogs.split("\n").length} lignes)
              </summary>
              <pre className="mt-2 max-h-64 overflow-auto border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg)] p-2 text-[9px] font-mono leading-relaxed text-[color:var(--color-cyber-muted)]">
                {buildLogs}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}

// ────────────────────────────────── status panel ──────────────────────

function StatusPanel({ onChanged }: { onChanged: () => void }) {
  const queryClient = useQueryClient();
  const q = useQuery({
    queryKey: ["forti", "status"],
    queryFn: getFortinetStatus,
    refetchInterval: 5_000,
  });
  const disc = useMutation({
    mutationFn: disconnectFortinet,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["forti"] });
      onChanged();
    },
  });
  const reconcile = useMutation({
    mutationFn: reconcileFortinetRouting,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["forti"] });
    },
  });

  const st = q.data;
  return (
    <article className="cyber-card p-5">
      <header className="mb-3 flex items-baseline justify-between gap-3">
        <div className="cyber-label flex items-center gap-2">
          <ShieldCheck className="cyber-glow h-3 w-3" />
          État du tunnel
        </div>
        {st && (
          <span className={cn("cyber-chip", STATE_CHIP[st.state])}>
            {STATE_LABEL[st.state]}
          </span>
        )}
      </header>
      {q.isLoading && (
        <p className="text-xs text-[color:var(--color-cyber-muted)]">
          probe SSH en cours…
        </p>
      )}
      {q.error && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(q.error)}
        </p>
      )}
      {st && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
          <dt className="cyber-label !text-[9px]">config active</dt>
          <dd className="font-mono">{st.slug || "—"}</dd>
          <dt className="cyber-label !text-[9px]">iface ppp</dt>
          <dd className="font-mono">{st.ppp_iface || "—"}</dd>
          <dt className="cyber-label !text-[9px]">IP tunnel</dt>
          <dd className="font-mono">{st.tunnel_ip || "—"}</dd>
          <dt className="cyber-label !text-[9px]">peer</dt>
          <dd className="font-mono">{st.gateway_ip || "—"}</dd>
          <dt className="cyber-label !text-[9px]">trafic</dt>
          <dd className="font-mono">
            ↓ {fmtBytes(st.rx_bytes)} · ↑ {fmtBytes(st.tx_bytes)}
          </dd>
          <dt className="cyber-label !text-[9px]">uptime</dt>
          <dd className="font-mono">
            {st.uptime_seconds > 0 ? fmtUptime(st.uptime_seconds) : "—"}
          </dd>
        </dl>
      )}
      <footer className="mt-4 flex flex-wrap gap-2">
        {st?.state === "up" && (
          <button
            type="button"
            disabled={disc.isPending}
            onClick={() => disc.mutate()}
            className="cyber-button inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px] disabled:opacity-50"
          >
            <Power className="h-3 w-3" />
            {disc.isPending ? "déconnexion…" : "Déconnecter"}
          </button>
        )}
        <button
          type="button"
          disabled={reconcile.isPending}
          onClick={() => reconcile.mutate()}
          className="inline-flex items-center gap-1.5 border border-[color:var(--color-cyber-border-strong)] px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-50"
          title="Re-pousser les règles per-network egress dans firewall.user"
        >
          <RefreshCw className={cn("h-3 w-3", reconcile.isPending && "animate-spin")} />
          re-sync routing
        </button>
      </footer>
      {disc.error && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(disc.error)}
        </p>
      )}
      {reconcile.data && (
        <p className="mt-3 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
          ▸ {reconcile.data.applied_lines} règles | nets opt-in :{" "}
          {reconcile.data.networks.join(", ") || "—"}
        </p>
      )}
    </article>
  );
}

// Le flux de connexion vit désormais sur sa page dédiée
// (/vpn/fortinet/connect — saisie identifiants + 2FA + status + logs).
// L'ancienne ConnectModal a été retirée pour éviter deux entrées
// concurrentes qui n'auraient pas le même comportement (notamment vis-à-
// vis de la persistance des credentials).

// ────────────────────────────────── config form ───────────────────────

function ConfigForm({
  initial,
  onClose,
  onSaved,
}: {
  initial: FortinetConfigPublic | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const editing = initial !== null;
  const [slug, setSlug] = useState(initial?.slug ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [host, setHost] = useState(initial?.gateway_host ?? "");
  const [port, setPort] = useState<number>(initial?.gateway_port ?? 443);
  const [pin, setPin] = useState(initial?.trusted_cert_sha256 ?? "");
  const [ca, setCa] = useState("");
  const [notes, setNotes] = useState(initial?.notes ?? "");

  const create = useMutation({
    mutationFn: () =>
      createFortinetConfig({
        slug,
        display_name: displayName,
        gateway_host: host,
        gateway_port: port,
        // username + password not persisted from this page — typed on
        // the dedicated login page at every connect.
        username: "",
        password: "",
        trusted_cert_sha256: pin,
        ca_cert_pem: ca,
        notes,
      }),
    onSuccess: () => {
      onSaved();
      onClose();
    },
  });
  const update = useMutation({
    mutationFn: () =>
      patchFortinetConfig(slug, {
        display_name: displayName,
        gateway_host: host,
        gateway_port: port,
        trusted_cert_sha256: pin,
        ca_cert_pem: ca || undefined,
        notes,
      }),
    onSuccess: () => {
      onSaved();
      onClose();
    },
  });
  const mut = editing ? update : create;

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    mut.mutate();
  }

  return (
    <form
      onSubmit={onSubmit}
      className="cyber-card cyber-card-accent space-y-4 p-5"
    >
      <header className="flex items-center justify-between">
        <h3 className="cyber-display cyber-glow text-base">
          {editing ? `EDIT · ${slug}` : "NEW · forti config"}
        </h3>
        <button
          type="button"
          onClick={onClose}
          className="border border-transparent p-1.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
        >
          <X className="h-4 w-4" />
        </button>
      </header>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="cyber-label mb-1.5 block">slug</span>
          <input
            type="text"
            required
            disabled={editing}
            value={slug}
            onChange={(e) =>
              setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))
            }
            placeholder="ex. corp-pro"
            className="cyber-input w-full py-2 px-3 text-sm font-mono disabled:opacity-50"
          />
        </label>
        <label className="block">
          <span className="cyber-label mb-1.5 block">nom affiché</span>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="ex. Corporate Pro"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
        <label className="col-span-2 block">
          <span className="cyber-label mb-1.5 block">gateway host</span>
          <input
            type="text"
            required
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="vpn.example.com"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
        <label className="block">
          <span className="cyber-label mb-1.5 block">port</span>
          <input
            type="number"
            value={port}
            min={1}
            max={65535}
            onChange={(e) => setPort(parseInt(e.target.value) || 443)}
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
        {/* username + password volontairement absents — saisis sur la
            page de connexion (« VPN → Fortinet — connexion ») à chaque
            session. Évite de persister des identifiants au repos. */}
        <label className="col-span-2 block">
          <span className="cyber-label mb-1.5 block">
            trusted cert SHA256 (pin — laisser vide = pas de pinning)
          </span>
          <input
            type="text"
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            placeholder="AA:BB:CC:... ou 64 chars hex"
            className="cyber-input w-full py-2 px-3 text-xs font-mono"
          />
        </label>
        <label className="col-span-2 block">
          <span className="cyber-label mb-1.5 block">
            CA cert PEM (optionnel — pour les CAs corporate self-signed)
          </span>
          <textarea
            value={ca}
            onChange={(e) => setCa(e.target.value)}
            rows={4}
            placeholder="-----BEGIN CERTIFICATE-----..."
            className="cyber-input w-full py-2 px-3 text-[10px] font-mono"
          />
        </label>
        <label className="col-span-2 block">
          <span className="cyber-label mb-1.5 block">notes</span>
          <input
            type="text"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="cyber-input w-full py-2 px-3 text-sm"
          />
        </label>
      </div>
      <div className="flex gap-2 pt-2">
        <button
          type="submit"
          disabled={mut.isPending || !slug || !host}
          className="cyber-button px-4 py-2 text-xs disabled:opacity-50"
        >
          {mut.isPending ? "saving…" : editing ? "Enregistrer" : "Créer"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="border border-[color:var(--color-cyber-border-strong)] px-4 py-2 text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
        >
          Annuler
        </button>
      </div>
      {mut.error && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(mut.error)}
        </p>
      )}
    </form>
  );
}

// ────────────────────────────────── config card ───────────────────────

function ConfigCard({
  config,
  onEdit,
  onChanged,
}: {
  config: FortinetConfigPublic;
  onEdit: () => void;
  onChanged: () => void;
}) {
  const del = useMutation({
    mutationFn: () => deleteFortinetConfig(config.slug),
    onSuccess: onChanged,
  });
  return (
    <article className="cyber-card p-4">
      <header className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]">
          <Lock className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <h3 className="cyber-display cyber-glow text-base">
              {config.slug}
            </h3>
            <span className={cn("cyber-chip", STATE_CHIP[config.last_status])}>
              {STATE_LABEL[config.last_status]}
            </span>
            {config.trusted_cert_sha256 && (
              <span className="cyber-chip cyber-chip-ok inline-flex items-center gap-1">
                <CheckCircle2 className="h-2.5 w-2.5" />
                pin TLS
              </span>
            )}
            {config.has_ca_cert && (
              <span className="cyber-chip">CA pinned</span>
            )}
          </div>
          <p className="mt-1 text-xs text-[color:var(--color-cyber-muted)]">
            {config.display_name || "—"}
          </p>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
            <dt className="cyber-label !text-[9px]">gateway</dt>
            <dd className="font-mono">
              {config.gateway_host}:{config.gateway_port}
            </dd>
            {config.last_connected_at && (
              <>
                <dt className="cyber-label !text-[9px]">dernière connexion</dt>
                <dd className="font-mono">
                  {formatDate(config.last_connected_at)}
                </dd>
              </>
            )}
            {config.last_error && (
              <>
                <dt className="cyber-label !text-[9px] text-[color:var(--color-cyber-accent)]">
                  dernière erreur
                </dt>
                <dd className="text-[10px] italic text-[color:var(--color-cyber-accent)]">
                  {config.last_error}
                </dd>
              </>
            )}
          </dl>
          {config.notes && (
            <p className="mt-2 text-[10px] italic text-[color:var(--color-cyber-dim)]">
              {config.notes}
            </p>
          )}
        </div>
      </header>
      <footer className="mt-4 flex flex-wrap gap-2">
        <Link
          to="/vpn/fortinet/connect"
          className="cyber-button inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px]"
          title="Saisie des identifiants + OTP sur la page de connexion"
        >
          <Power className="h-3 w-3" />
          Connecter
        </Link>
        <button
          type="button"
          onClick={onEdit}
          className="inline-flex items-center gap-1.5 border border-[color:var(--color-cyber-border-strong)] px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
        >
          <Pencil className="h-3 w-3" />
          Éditer
        </button>
        <button
          type="button"
          disabled={del.isPending}
          onClick={() => {
            if (confirm(`Supprimer la config "${config.slug}" ?`)) {
              del.mutate();
            }
          }}
          className="ml-auto inline-flex items-center gap-1.5 border border-transparent px-3 py-1.5 text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-50"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </footer>
      {del.error && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(del.error)}
        </p>
      )}
    </article>
  );
}

// ────────────────────────────────── page ──────────────────────────────

export default function Fortinet() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<FortinetConfigPublic | null>(null);

  const q = useQuery({
    queryKey: ["forti", "configs"],
    queryFn: listFortinetConfigs,
  });
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["forti"] });

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-6 flex items-end justify-between gap-4">
        <div>
          <div className="cyber-label mb-2 flex items-center gap-2">
            <Lock className="cyber-glow h-3 w-3" />
            corporate ssl vpn · fortinet
          </div>
          <h1
            className="cyber-display cyber-glitch text-4xl"
            data-text="FORTINET"
          >
            FORTINET
          </h1>
          <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
            tunnel ssl vpn vers la passerelle FortiGate · routage opt-in par réseau
          </p>
        </div>
        {!creating && !editing && (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="cyber-button inline-flex items-center gap-2 px-4 py-2.5 text-xs"
          >
            <Plus className="h-3.5 w-3.5" />
            Nouvelle config
          </button>
        )}
      </header>

      <section className="mb-6">
        <PreflightBanner />
      </section>

      <section className="mb-6">
        <StatusPanel onChanged={refresh} />
      </section>

      {creating && (
        <section className="mb-6">
          <ConfigForm
            initial={null}
            onClose={() => setCreating(false)}
            onSaved={refresh}
          />
        </section>
      )}
      {editing && (
        <section className="mb-6">
          <ConfigForm
            initial={editing}
            onClose={() => setEditing(null)}
            onSaved={refresh}
          />
        </section>
      )}

      {q.isLoading && (
        <p className="cyber-label cyber-cursor">loading…</p>
      )}
      {q.error && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(q.error)}
        </p>
      )}
      {q.data && q.data.length === 0 && !creating && (
        <p className="text-xs text-[color:var(--color-cyber-muted)]">
          Aucune configuration enregistrée. En créer une pour permettre la connexion au FortiGate.
        </p>
      )}
      {q.data && q.data.length > 0 && (
        <div className="space-y-3">
          {q.data.map((c) => (
            <ConfigCard
              key={c.slug}
              config={c}
              onEdit={() => setEditing(c)}
              onChanged={refresh}
            />
          ))}
        </div>
      )}
    </div>
  );
}
