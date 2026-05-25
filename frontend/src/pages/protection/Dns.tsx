import { useMemo, useState } from "react";
import { AxiosError } from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Flag,
  Globe,
  HeartHandshake,
  Info,
  Lock,
  RefreshCw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  Trash2,
  Zap,
} from "lucide-react";

import DnsThreatModelModal from "@/components/DnsThreatModelModal";

import { listNetworks } from "@/api/networks";
import {
  type LevelPatchBody,
  disableAntiBypass,
  enableAntiBypass,
  getAdGuardBlockedServicesCatalog,
  getAntiBypassStatus,
  getDnsCatalog,
  getSecurityLevels,
  listProtections,
  patchSecurityLevel,
  reapplyAllProtections,
  removeProtection,
  resetSecurityLevel,
  setProtection,
} from "@/api/dns";
import type {
  DnsProvider,
  NetworkProtection,
  SecurityLevel,
} from "@/types/dns";
import { createPortal } from "react-dom";

const LEVEL_ICONS: Record<string, typeof Shield> = {
  Zap,
  Shield,
  ShieldCheck,
  HeartHandshake,
  Flag,
  ShieldAlert,
};

function LevelIcon({ name, className }: { name: string; className?: string }) {
  const Cmp = LEVEL_ICONS[name] ?? Shield;
  return <Cmp className={className} />;
}

function transportBadge(t: string) {
  const colors: Record<string, string> = {
    DoT: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
    DoH: "bg-blue-500/20 text-blue-300 border-blue-500/40",
    UDP: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  };
  return colors[t] ?? "bg-slate-500/20 text-[color:var(--color-cyber-fg)] border-slate-500/40";
}

export default function DnsPage() {
  const qc = useQueryClient();
  const [configuringNet, setConfiguringNet] = useState<string | null>(null);
  const [editingLevel, setEditingLevel] = useState<string | null>(null);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [threatModelOpen, setThreatModelOpen] = useState(false);

  const networks = useQuery({
    queryKey: ["networks"],
    queryFn: listNetworks,
  });
  const levels = useQuery({
    queryKey: ["dns", "levels"],
    queryFn: getSecurityLevels,
  });
  const protections = useQuery({
    queryKey: ["dns", "protections"],
    queryFn: listProtections,
  });
  const catalog = useQuery({
    queryKey: ["dns", "catalog"],
    queryFn: () => getDnsCatalog(),
  });

  const reapplyMut = useMutation({
    mutationFn: reapplyAllProtections,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dns", "protections"] }),
  });

  const protectionByNetwork = useMemo(() => {
    const map = new Map<string, NetworkProtection>();
    for (const p of protections.data?.protections ?? []) {
      map.set(p.network_slug, p);
    }
    return map;
  }, [protections.data]);

  const levelBySlug = useMemo(() => {
    const map = new Map<string, SecurityLevel>();
    for (const l of levels.data?.levels ?? []) map.set(l.slug, l);
    return map;
  }, [levels.data]);

  const providerBySlug = useMemo(() => {
    const map = new Map<string, DnsProvider>();
    for (const p of catalog.data?.providers ?? []) map.set(p.slug, p);
    return map;
  }, [catalog.data]);

  const networksList = networks.data ?? [];
  const levelsList = levels.data?.levels ?? [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-[color:var(--color-cyber-fg)]">
            <Globe className="h-6 w-6 text-cyan-400" />
            Protection DNS
          </h1>
          <p className="mt-1 text-sm text-[color:var(--color-cyber-muted)]">
            Résolveurs DNS sécurisés (DoT/DoH) et niveaux de protection
            appliqués par-réseau via AdGuard Clients.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setThreatModelOpen(true)}
            className="flex items-center gap-2 rounded-md border border-cyan-500/40 bg-cyan-500/10 px-3 py-2 text-sm text-cyan-300 transition hover:bg-cyan-500/20"
            title="Ouvre une explication détaillée des menaces DNS et des protections actives"
          >
            <Info className="h-4 w-4" />
            Modèle de menace
          </button>
          <button
            onClick={() => reapplyMut.mutate()}
            disabled={reapplyMut.isPending}
            className="flex items-center gap-2 rounded-md border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-3 py-2 text-sm text-[color:var(--color-cyber-fg)] transition hover:border-cyan-500 hover:bg-slate-700 disabled:opacity-50"
            title="Re-pousse toutes les protections vers AdGuard (utile après restart)"
          >
            <RefreshCw className={`h-4 w-4 ${reapplyMut.isPending ? "animate-spin" : ""}`} />
            Re-appliquer tout
          </button>
        </div>
      </div>

      {reapplyMut.isError && (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">
          Erreur reapply : {(reapplyMut.error as AxiosError)?.message ?? "?"}
        </div>
      )}
      {reapplyMut.isSuccess && reapplyMut.data && (
        <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-300">
          Re-appliqué: {reapplyMut.data.applied.length} ok,{" "}
          {reapplyMut.data.skipped.length} skip, {reapplyMut.data.errors.length} err
        </div>
      )}

      {/* Niveaux de sécurité */}
      <section>
        <h2 className="mb-3 text-lg font-semibold text-[color:var(--color-cyber-fg)]">
          Niveaux de sécurité
        </h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {levelsList.map((level) => (
            <LevelCard
              key={level.slug}
              level={level}
              onEdit={() => setEditingLevel(level.slug)}
            />
          ))}
        </div>
      </section>

      {/* Anti-bypass: block TCP/853 + GL.iNet leak rules */}
      <AntiBypassSection />

      {/* Réseaux */}
      <section>
        <h2 className="mb-3 text-lg font-semibold text-[color:var(--color-cyber-fg)]">
          Application par réseau
        </h2>
        {networks.isLoading || protections.isLoading ? (
          <div className="text-sm text-[color:var(--color-cyber-dim)]">Chargement…</div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {networksList.map((net) => (
              <NetworkProtectionCard
                key={net.slug}
                networkSlug={net.slug}
                networkName={net.display_name}
                networkCidr={net.subnet_cidr}
                isolated={net.isolated_from_lan}
                protection={protectionByNetwork.get(net.slug)}
                onConfigure={() => setConfiguringNet(net.slug)}
              />
            ))}
          </div>
        )}
      </section>

      {/* Catalogue providers (collapsible) */}
      <section>
        <button
          onClick={() => setCatalogOpen((v) => !v)}
          className="flex w-full items-center justify-between rounded-md border border-[color:var(--color-cyber-border-strong)] bg-slate-800/50 px-4 py-3 text-left text-[color:var(--color-cyber-fg)] transition hover:bg-[color:var(--color-cyber-surface-2)]"
        >
          <span className="flex items-center gap-2 text-lg font-semibold">
            <Lock className="h-5 w-5 text-cyan-400" />
            Catalogue providers ({catalog.data?.total ?? 0})
          </span>
          {catalogOpen ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
        </button>
        {catalogOpen && catalog.data && (
          <div className="mt-3">
            <ProviderCatalogTable providers={catalog.data.providers} />
          </div>
        )}
      </section>

      {/* Modal configuration */}
      {configuringNet && (
        <ConfigureProtectionModal
          networkSlug={configuringNet}
          networkName={
            networksList.find((n) => n.slug === configuringNet)?.display_name ??
            configuringNet
          }
          currentProtection={protectionByNetwork.get(configuringNet)}
          levels={levelsList}
          providers={catalog.data?.providers ?? []}
          providerBySlug={providerBySlug}
          levelBySlug={levelBySlug}
          onClose={() => setConfiguringNet(null)}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ["dns", "protections"] });
            setConfiguringNet(null);
          }}
        />
      )}

      {/* Modal pédagogique threat model */}
      {threatModelOpen && (
        <DnsThreatModelModal onClose={() => setThreatModelOpen(false)} />
      )}

      {/* Modal édition d'un niveau */}
      {editingLevel && levelBySlug.get(editingLevel) && (
        <EditLevelModal
          level={levelBySlug.get(editingLevel)!}
          providers={catalog.data?.providers ?? []}
          onClose={() => setEditingLevel(null)}
          onSaved={() => {
            // Re-fetch levels (PATCH may have changed config) AND protections
            // (server-side reapply may have changed the active provider per
            // network).
            qc.invalidateQueries({ queryKey: ["dns", "levels"] });
            qc.invalidateQueries({ queryKey: ["dns", "protections"] });
            setEditingLevel(null);
          }}
        />
      )}
    </div>
  );
}

function LevelCard({
  level,
  onEdit,
}: {
  level: SecurityLevel;
  onEdit: () => void;
}) {
  const components: string[] = [];
  components.push(`Default: ${level.default_provider_slug}`);
  if (level.require_dot) components.push("DoT requis");
  if (level.require_dnssec) components.push("DNSSEC requis");
  if (level.eu_only) components.push("EU only");
  if (level.adguard_filtering) components.push("AdGuard filter ON");
  if (level.parental_control) components.push("Parental");
  if (level.safe_search) components.push("Safe Search forcé");
  if (level.safe_browsing) components.push("Safe Browsing");
  if (level.blocked_services.length > 0)
    components.push(`Blocked: ${level.blocked_services.length} services`);
  if (level.adguard_blocklist_slugs.length > 0)
    components.push(`+${level.adguard_blocklist_slugs.length} blocklists`);

  return (
    <div
      className="flex flex-col rounded-lg border bg-slate-900/60 p-4 shadow-sm transition hover:shadow-md"
      style={{ borderColor: `${level.color}66` }}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <LevelIcon name={level.icon} className="h-5 w-5" />
          <h3 className="text-base font-semibold text-[color:var(--color-cyber-fg)]">
            {level.name}
          </h3>
        </div>
        <button
          onClick={onEdit}
          className="rounded border border-[color:var(--color-cyber-border-strong)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] transition hover:border-cyan-500 hover:text-cyan-300"
          title="Éditer ce niveau (provider, blocklists, toggles…)"
        >
          Éditer
        </button>
      </div>
      <p className="mb-3 text-xs text-[color:var(--color-cyber-muted)]">{level.description}</p>
      <ul className="space-y-1 text-xs text-[color:var(--color-cyber-fg)]">
        {components.map((c) => (
          <li key={c} className="flex items-start gap-1.5">
            <span style={{ color: level.color }}>▸</span>
            <span>{c}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Edit a security level: tunes default provider, allowed providers, blocked
// services (multi-select from AdGuard's authoritative 118-entry catalog),
// and the various toggles. Save triggers backend-side reapply on every
// network using this level — no separate action needed.
function EditLevelModal({
  level,
  providers,
  onClose,
  onSaved,
}: {
  level: SecurityLevel;
  providers: DnsProvider[];
  onClose: () => void;
  onSaved: () => void;
}) {
  // Local form state, initialized from the level.
  const [description, setDescription] = useState(level.description);
  const [defaultProvider, setDefaultProvider] = useState(level.default_provider_slug);
  const [allowed, setAllowed] = useState<Set<string>>(
    new Set(level.allowed_provider_slugs),
  );
  const [blocked, setBlocked] = useState<Set<string>>(
    new Set(level.blocked_services),
  );
  const [serviceFilter, setServiceFilter] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const [toggles, setToggles] = useState({
    adguard_filtering: level.adguard_filtering,
    safe_browsing: level.safe_browsing,
    parental_control: level.parental_control,
    safe_search: level.safe_search,
    require_dot: level.require_dot,
    require_dnssec: level.require_dnssec,
    eu_only: level.eu_only,
  });

  // AdGuard authoritative blocked_services catalog (~118 entries).
  const services = useQuery({
    queryKey: ["adguard", "blocked-services-catalog"],
    queryFn: getAdGuardBlockedServicesCatalog,
    staleTime: 5 * 60 * 1000,  // cache 5min, change very rarely
  });

  // Restrict the "allowed providers" list to ones that satisfy the toggles
  // (eu_only / require_dot / require_dnssec). Highlight invalid choices.
  const providersFiltered = useMemo(() => {
    return providers.filter((p) => {
      if (providerFilter && !`${p.name} ${p.organization} ${p.slug}`.toLowerCase().includes(providerFilter.toLowerCase())) {
        return false;
      }
      return true;
    });
  }, [providers, providerFilter]);

  const servicesFiltered = useMemo(() => {
    const all = services.data?.services ?? [];
    if (!serviceFilter) return all;
    const q = serviceFilter.toLowerCase();
    return all.filter(
      (s) => s.id.toLowerCase().includes(q) || (s.name ?? "").toLowerCase().includes(q),
    );
  }, [services.data, serviceFilter]);

  const saveMut = useMutation({
    mutationFn: () => {
      const body: LevelPatchBody = {
        description,
        default_provider_slug: defaultProvider,
        allowed_provider_slugs: Array.from(allowed).sort(),
        blocked_services: Array.from(blocked).sort(),
        ...toggles,
      };
      return patchSecurityLevel(level.slug, body);
    },
    onSuccess: onSaved,
  });

  const resetMut = useMutation({
    mutationFn: () => resetSecurityLevel(level.slug),
    onSuccess: onSaved,
  });

  function toggleAllowed(slug: string) {
    setAllowed((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  function toggleBlocked(id: string) {
    setBlocked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-lg border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-bold text-[color:var(--color-cyber-fg)]">
            <LevelIcon name={level.icon} className="h-5 w-5" />
            Éditer le niveau <span style={{ color: level.color }}>{level.name}</span>
          </h2>
          <span className="rounded bg-[color:var(--color-cyber-surface-2)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-dim)]">
            slug: {level.slug}
          </span>
        </div>

        {/* Description */}
        <label className="mb-1 block text-sm font-medium text-[color:var(--color-cyber-fg)]">
          Description
        </label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          className="mb-4 w-full rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-3 py-2 text-sm text-[color:var(--color-cyber-fg)]"
        />

        {/* Toggles */}
        <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {([
            ["adguard_filtering", "AdGuard filter"],
            ["safe_browsing", "Safe Browsing"],
            ["parental_control", "Parental control"],
            ["safe_search", "Safe Search forcé"],
            ["require_dot", "Require DoT"],
            ["require_dnssec", "Require DNSSEC"],
            ["eu_only", "EU only"],
          ] as const).map(([key, label]) => (
            <label
              key={key}
              className="flex items-center gap-2 rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)]/40 px-2 py-1.5 text-xs text-[color:var(--color-cyber-fg)]"
            >
              <input
                type="checkbox"
                checked={toggles[key]}
                onChange={(e) =>
                  setToggles((t) => ({ ...t, [key]: e.target.checked }))
                }
              />
              {label}
            </label>
          ))}
        </div>

        {/* Default provider */}
        <label className="mb-1 block text-sm font-medium text-[color:var(--color-cyber-fg)]">
          Provider par défaut
        </label>
        <select
          value={defaultProvider}
          onChange={(e) => setDefaultProvider(e.target.value)}
          className="mb-4 w-full rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-3 py-2 text-sm text-[color:var(--color-cyber-fg)]"
        >
          {providers
            .filter((p) => allowed.has(p.slug) || p.slug === defaultProvider)
            .map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.name} ({p.country}) — {p.filter_profile}
                {p.recommended ? " ★" : ""}
              </option>
            ))}
        </select>

        {/* Allowed providers (multi-select via checkboxes) */}
        <div className="mb-4">
          <div className="mb-1 flex items-center justify-between">
            <label className="text-sm font-medium text-[color:var(--color-cyber-fg)]">
              Providers autorisés ({allowed.size})
            </label>
            <input
              type="text"
              placeholder="filtrer…"
              value={providerFilter}
              onChange={(e) => setProviderFilter(e.target.value)}
              className="rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-2 py-0.5 text-xs text-[color:var(--color-cyber-fg)]"
            />
          </div>
          <div className="grid max-h-48 grid-cols-1 gap-1 overflow-y-auto rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)]/40 p-2 sm:grid-cols-2">
            {providersFiltered.map((p) => (
              <label
                key={p.slug}
                className="flex items-center gap-2 rounded px-1.5 py-0.5 text-xs text-[color:var(--color-cyber-fg)] hover:bg-slate-700/40"
              >
                <input
                  type="checkbox"
                  checked={allowed.has(p.slug)}
                  onChange={() => toggleAllowed(p.slug)}
                />
                <span className="flex-1 truncate">
                  {p.name}{" "}
                  <span className="text-[color:var(--color-cyber-dim)]">({p.country})</span>
                </span>
                {p.recommended && <span className="text-amber-400">★</span>}
              </label>
            ))}
          </div>
        </div>

        {/* Blocked services (AdGuard authoritative catalog) */}
        <div className="mb-4">
          <div className="mb-1 flex items-center justify-between">
            <label className="text-sm font-medium text-[color:var(--color-cyber-fg)]">
              Services bloqués AdGuard ({blocked.size})
            </label>
            <input
              type="text"
              placeholder="filtrer (tiktok, snap, gambling…)"
              value={serviceFilter}
              onChange={(e) => setServiceFilter(e.target.value)}
              className="w-60 rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-2 py-0.5 text-xs text-[color:var(--color-cyber-fg)]"
            />
          </div>
          <div className="grid max-h-48 grid-cols-2 gap-1 overflow-y-auto rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)]/40 p-2 sm:grid-cols-3 lg:grid-cols-4">
            {services.isLoading && (
              <span className="col-span-full text-xs text-[color:var(--color-cyber-dim)]">Chargement…</span>
            )}
            {servicesFiltered.map((s) => (
              <label
                key={s.id}
                className="flex items-center gap-2 rounded px-1.5 py-0.5 text-xs text-[color:var(--color-cyber-fg)] hover:bg-slate-700/40"
              >
                <input
                  type="checkbox"
                  checked={blocked.has(s.id)}
                  onChange={() => toggleBlocked(s.id)}
                />
                <span className="truncate">{s.name ?? s.id}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Error / success */}
        {saveMut.isError && (
          <div className="mb-3 flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>
              {(saveMut.error as AxiosError<{ detail: string }>)?.response?.data?.detail ??
                (saveMut.error as Error)?.message}
            </span>
          </div>
        )}
        {saveMut.isSuccess && saveMut.data && (
          <div className="mb-3 rounded border border-emerald-500/40 bg-emerald-500/10 p-2 text-xs text-emerald-300">
            Enregistré + {saveMut.data.reapply.applied.length} réseau(x) re-appliqué(s).
          </div>
        )}

        <div className="flex justify-between gap-2">
          <button
            onClick={() => {
              if (confirm(`Restaurer "${level.name}" à ses valeurs d'usine ?`))
                resetMut.mutate();
            }}
            disabled={resetMut.isPending}
            className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-300 hover:bg-amber-500/20 disabled:opacity-50"
            title="Restaure les valeurs initiales (FACTORY_LEVELS) et re-applique"
          >
            ↺ Reset usine
          </button>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-4 py-2 text-sm text-[color:var(--color-cyber-fg)] hover:bg-slate-700"
            >
              Annuler
            </button>
            <button
              onClick={() => saveMut.mutate()}
              disabled={saveMut.isPending}
              className="rounded border border-cyan-500/40 bg-cyan-500/20 px-4 py-2 text-sm text-cyan-200 hover:bg-cyan-500/30 disabled:opacity-50"
            >
              {saveMut.isPending ? "Enregistrement…" : "Enregistrer"}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function NetworkProtectionCard({
  networkSlug,
  networkName,
  networkCidr,
  isolated,
  protection,
  onConfigure,
}: {
  networkSlug: string;
  networkName: string;
  networkCidr: string;
  isolated: boolean;
  protection: NetworkProtection | undefined;
  onConfigure: () => void;
}) {
  const qc = useQueryClient();
  const removeMut = useMutation({
    mutationFn: () => removeProtection(networkSlug),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dns", "protections"] }),
  });

  return (
    <div className="rounded-lg border border-[color:var(--color-cyber-border-strong)] bg-slate-900/60 p-4">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold text-[color:var(--color-cyber-fg)]">{networkName}</h3>
          <p className="font-mono text-xs text-[color:var(--color-cyber-dim)]">
            {networkCidr} {isolated && "· isolé du LAN"}
          </p>
        </div>
        {protection ? (
          <CheckCircle2 className="h-5 w-5 text-emerald-400" />
        ) : (
          <ShieldOff className="h-5 w-5 text-[color:var(--color-cyber-dim)]" />
        )}
      </div>

      {protection ? (
        <div className="space-y-2 text-sm">
          <div className="flex items-center gap-2">
            <span className="text-[color:var(--color-cyber-muted)]">Niveau:</span>
            <span className="font-medium text-[color:var(--color-cyber-fg)]">{protection.level_name}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[color:var(--color-cyber-muted)]">Provider:</span>
            <span className="text-[color:var(--color-cyber-fg)]">{protection.provider_name}</span>
            {protection.provider_eu_based && (
              <Flag className="h-3.5 w-3.5 text-blue-400" />
            )}
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-[color:var(--color-cyber-muted)] text-xs">Transport:</span>
            {protection.upstream_transports.map((t) => (
              <span
                key={t}
                className={`rounded border px-1.5 py-0.5 text-[10px] font-mono uppercase ${transportBadge(t)}`}
              >
                {t}
              </span>
            ))}
          </div>
          <div className="mt-3 flex gap-2">
            <button
              onClick={onConfigure}
              className="flex-1 rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-1.5 text-xs text-cyan-300 hover:bg-cyan-500/20"
            >
              Modifier
            </button>
            <button
              onClick={() => {
                if (confirm(`Retirer la protection DNS de ${networkName} ?`)) removeMut.mutate();
              }}
              disabled={removeMut.isPending}
              className="rounded border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-xs text-red-300 hover:bg-red-500/20 disabled:opacity-50"
              title="Supprimer la protection (et le client AdGuard)"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-2">
          <p className="mb-2 text-xs text-[color:var(--color-cyber-dim)]">
            Aucune protection DNS configurée. Le réseau utilise le DNS Slate par défaut.
          </p>
          <button
            onClick={onConfigure}
            className="w-full rounded border border-cyan-500/40 bg-cyan-500/10 px-3 py-2 text-sm text-cyan-300 hover:bg-cyan-500/20"
          >
            Configurer
          </button>
        </div>
      )}
    </div>
  );
}

function ConfigureProtectionModal({
  networkSlug,
  networkName,
  currentProtection,
  levels,
  providers,
  providerBySlug,
  levelBySlug,
  onClose,
  onSaved,
}: {
  networkSlug: string;
  networkName: string;
  currentProtection: NetworkProtection | undefined;
  levels: SecurityLevel[];
  providers: DnsProvider[];
  providerBySlug: Map<string, DnsProvider>;
  levelBySlug: Map<string, SecurityLevel>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [levelSlug, setLevelSlug] = useState<string>(
    currentProtection?.level_slug ?? levels[0]?.slug ?? "",
  );
  const selectedLevel = levelBySlug.get(levelSlug);
  const [providerSlug, setProviderSlug] = useState<string>(
    currentProtection?.provider_slug ?? selectedLevel?.default_provider_slug ?? "",
  );

  // Filter providers by level constraints
  const allowedProviders = useMemo(() => {
    if (!selectedLevel) return [];
    const allowed = selectedLevel.allowed_provider_slugs.length
      ? selectedLevel.allowed_provider_slugs
      : providers.map((p) => p.slug);
    return providers.filter((p) => {
      if (!allowed.includes(p.slug)) return false;
      if (selectedLevel.require_dot && !p.dot_hostname) return false;
      if (selectedLevel.eu_only && !p.is_eu_based) return false;
      if (selectedLevel.require_dnssec && !p.supports_dnssec) return false;
      return true;
    });
  }, [selectedLevel, providers]);

  // When level changes, reset provider to its default
  function handleLevelChange(slug: string) {
    setLevelSlug(slug);
    const lv = levelBySlug.get(slug);
    if (lv) setProviderSlug(lv.default_provider_slug);
  }

  const saveMut = useMutation({
    mutationFn: () =>
      setProtection(networkSlug, {
        level_slug: levelSlug,
        provider_slug:
          providerSlug && providerSlug !== selectedLevel?.default_provider_slug
            ? providerSlug
            : null,
      }),
    onSuccess: onSaved,
  });

  const selectedProvider = providerBySlug.get(providerSlug);

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="w-full max-w-2xl rounded-lg border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-1 text-lg font-bold text-[color:var(--color-cyber-fg)]">
          Protection DNS — {networkName}
        </h2>
        <p className="mb-4 text-xs text-[color:var(--color-cyber-muted)]">
          Le niveau choisi crée un client AdGuard pour ce réseau. L'upstream
          encrypted (DoT/DoH) et les filtres sont appliqués selon le profil.
        </p>

        <label className="mb-1 block text-sm font-medium text-[color:var(--color-cyber-fg)]">
          Niveau de sécurité
        </label>
        <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-5">
          {levels.map((lv) => (
            <button
              key={lv.slug}
              onClick={() => handleLevelChange(lv.slug)}
              className={`flex flex-col items-center gap-1 rounded border-2 p-2 text-xs transition ${
                levelSlug === lv.slug
                  ? "bg-[color:var(--color-cyber-surface-2)] ring-2"
                  : "border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)]/40 hover:bg-[color:var(--color-cyber-surface-2)]"
              }`}
              style={
                levelSlug === lv.slug
                  ? { borderColor: lv.color, boxShadow: `0 0 0 2px ${lv.color}33` }
                  : {}
              }
            >
              <LevelIcon name={lv.icon} className="h-4 w-4" />
              <span className="font-medium text-[color:var(--color-cyber-fg)]">{lv.name}</span>
            </button>
          ))}
        </div>

        {selectedLevel && (
          <p className="mb-4 rounded bg-[color:var(--color-cyber-surface-2)]/40 p-2 text-xs text-[color:var(--color-cyber-muted)]">
            {selectedLevel.description}
          </p>
        )}

        <label className="mb-1 block text-sm font-medium text-[color:var(--color-cyber-fg)]">
          Provider DNS upstream
        </label>
        <select
          value={providerSlug}
          onChange={(e) => setProviderSlug(e.target.value)}
          className="mb-2 w-full rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-3 py-2 text-sm text-[color:var(--color-cyber-fg)]"
        >
          {allowedProviders.length === 0 && <option value="">— aucun provider compatible —</option>}
          {allowedProviders.map((p) => (
            <option key={p.slug} value={p.slug}>
              {p.name} ({p.country}) — {p.filter_profile}
              {p.recommended ? " ★" : ""}
            </option>
          ))}
        </select>

        {selectedProvider && (
          <div className="mb-4 rounded bg-[color:var(--color-cyber-surface-2)]/40 p-3 text-xs text-[color:var(--color-cyber-muted)]">
            <p className="mb-1">{selectedProvider.description}</p>
            <div className="flex flex-wrap gap-1">
              <span className="rounded bg-slate-700 px-1.5 py-0.5">
                Org: {selectedProvider.organization}
              </span>
              <span className="rounded bg-slate-700 px-1.5 py-0.5">
                Log: {selectedProvider.log_policy}
              </span>
              {selectedProvider.dot_hostname && (
                <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-emerald-300">
                  DoT
                </span>
              )}
              {selectedProvider.doh_url && (
                <span className="rounded bg-blue-500/20 px-1.5 py-0.5 text-blue-300">
                  DoH
                </span>
              )}
              {selectedProvider.supports_dnssec && (
                <span className="rounded bg-purple-500/20 px-1.5 py-0.5 text-purple-300">
                  DNSSEC
                </span>
              )}
              {selectedProvider.is_eu_based && (
                <span className="rounded bg-blue-500/20 px-1.5 py-0.5 text-blue-300">
                  EU
                </span>
              )}
            </div>
          </div>
        )}

        {saveMut.isError && (
          <div className="mb-3 flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>
              {(saveMut.error as AxiosError<{ detail: string }>)?.response?.data?.detail ??
                (saveMut.error as Error)?.message ??
                "Erreur inconnue"}
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
            disabled={saveMut.isPending || !providerSlug}
            className="rounded border border-cyan-500/40 bg-cyan-500/20 px-4 py-2 text-sm text-cyan-200 hover:bg-cyan-500/30 disabled:opacity-50"
          >
            {saveMut.isPending ? "Application…" : "Appliquer"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function ProviderCatalogTable({ providers }: { providers: DnsProvider[] }) {
  const [euOnly, setEuOnly] = useState(false);
  const [filterProfile, setFilterProfile] = useState<string>("all");

  const filtered = useMemo(() => {
    return providers.filter((p) => {
      if (euOnly && !p.is_eu_based) return false;
      if (filterProfile !== "all" && p.filter_profile !== filterProfile) return false;
      return true;
    });
  }, [providers, euOnly, filterProfile]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-3 text-sm">
        <label className="flex items-center gap-1.5 text-[color:var(--color-cyber-fg)]">
          <input
            type="checkbox"
            checked={euOnly}
            onChange={(e) => setEuOnly(e.target.checked)}
            className="rounded border-slate-600"
          />
          EU only
        </label>
        <select
          value={filterProfile}
          onChange={(e) => setFilterProfile(e.target.value)}
          className="rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-2 py-1 text-[color:var(--color-cyber-fg)]"
        >
          <option value="all">Tous filtres</option>
          <option value="none">No filter</option>
          <option value="malware">Malware</option>
          <option value="adblock">Adblock</option>
          <option value="family">Family</option>
        </select>
        <span className="ml-auto text-xs text-[color:var(--color-cyber-dim)]">
          {filtered.length} / {providers.length}
        </span>
      </div>
      <div className="overflow-x-auto rounded-lg border border-[color:var(--color-cyber-border-strong)]">
        <table className="w-full text-sm">
          <thead className="bg-[color:var(--color-cyber-surface-2)] text-xs uppercase text-[color:var(--color-cyber-muted)]">
            <tr>
              <th className="px-3 py-2 text-left">Provider</th>
              <th className="px-3 py-2 text-left">Org</th>
              <th className="px-3 py-2 text-left">Country</th>
              <th className="px-3 py-2 text-left">Filter</th>
              <th className="px-3 py-2 text-left">Log</th>
              <th className="px-3 py-2 text-center">DoT</th>
              <th className="px-3 py-2 text-center">DoH</th>
              <th className="px-3 py-2 text-center">DNSSEC</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {filtered.map((p) => (
              <tr key={p.slug} className="text-[color:var(--color-cyber-fg)] hover:bg-[color:var(--color-cyber-surface-2)]/30">
                <td className="px-3 py-2">
                  <div className="flex items-center gap-1.5">
                    {p.recommended && <span className="text-amber-400">★</span>}
                    <span className="font-medium text-[color:var(--color-cyber-fg)]">{p.name}</span>
                  </div>
                  <div className="font-mono text-[10px] text-[color:var(--color-cyber-dim)]">{p.ipv4_primary}</div>
                </td>
                <td className="px-3 py-2 text-xs">{p.organization}</td>
                <td className="px-3 py-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] ${
                      p.is_eu_based ? "bg-blue-500/20 text-blue-300" : "bg-slate-700 text-[color:var(--color-cyber-fg)]"
                    }`}
                  >
                    {p.country}
                  </span>
                </td>
                <td className="px-3 py-2 text-xs">{p.filter_profile}</td>
                <td className="px-3 py-2 text-xs">{p.log_policy}</td>
                <td className="px-3 py-2 text-center">
                  {p.dot_hostname ? <CheckCircle2 className="mx-auto h-3.5 w-3.5 text-emerald-400" /> : "—"}
                </td>
                <td className="px-3 py-2 text-center">
                  {p.doh_url ? <CheckCircle2 className="mx-auto h-3.5 w-3.5 text-emerald-400" /> : "—"}
                </td>
                <td className="px-3 py-2 text-center">
                  {p.supports_dnssec ? <CheckCircle2 className="mx-auto h-3.5 w-3.5 text-emerald-400" /> : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * Section dédiée aux mécanismes anti-bypass DNS :
 *   - règle firewall TCP/853 LAN→WAN (bloque le DoT côté client)
 *   - activation des règles GL.iNet `*_drop_leaked_*` préinstallées
 *
 * La blocklist DoH (HaGeZi DoH/VPN) est, elle, une feed AdGuard normale —
 * activable depuis la page AdGuard. On la mentionne ici comme complément
 * naturel mais on ne la togglie pas depuis cette section pour éviter de
 * dupliquer la logique de feed management.
 */
function AntiBypassSection() {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["dns", "anti-bypass"],
    queryFn: getAntiBypassStatus,
  });
  const enableMut = useMutation({
    mutationFn: enableAntiBypass,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dns", "anti-bypass"] }),
  });
  const disableMut = useMutation({
    mutationFn: disableAntiBypass,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dns", "anti-bypass"] }),
  });

  const data = status.data;
  const pending = enableMut.isPending || disableMut.isPending;

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-[color:var(--color-cyber-fg)]">
          Anti-bypass DoT / DoH
        </h2>
        {data && (
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${
              data.all_active
                ? "bg-emerald-500/20 text-emerald-300"
                : data.any_active
                ? "bg-amber-500/20 text-amber-300"
                : "bg-slate-700 text-[color:var(--color-cyber-muted)]"
            }`}
          >
            {data.all_active
              ? "Toutes règles actives"
              : data.any_active
              ? "Partiel"
              : "Inactif"}
          </span>
        )}
      </div>

      <div className="rounded-lg border border-[color:var(--color-cyber-border-strong)] bg-slate-900/60 p-4">
        <p className="mb-3 text-sm text-[color:var(--color-cyber-fg)]">
          Empêche les clients de contourner le résolveur local en utilisant
          leurs propres canaux DNS chiffrés. Combine deux mécanismes
          complémentaires :
        </p>

        <div className="mb-4 space-y-2">
          <BypassMechanism
            label="Bloquer le port TCP/853 (LAN → WAN)"
            description="Empêche les navigateurs et applications qui utilisent un résolveur DoT propre (Cloudflare, Quad9 direct, etc.) de contourner AdGuard. Ces clients basculent automatiquement sur le DNS système."
            active={data?.custom_block_dot_active ?? false}
          />
          <BypassMechanism
            label="Activer les règles anti-fuite GL.iNet préinstallées"
            description="Le firmware GL.iNet inclut des règles drop_leaked_dns/adgdns pour les zones LAN, guest, wgserver et ovpnserver mais les laisse désactivées par défaut. Empêche les fuites DNS lors de la rotation de tunnels."
            active={
              data
                ? Object.values(data.gl_rules_enabled).filter((v) => v).length > 0
                : false
            }
            subItems={
              data
                ? Object.entries(data.gl_rules_enabled).map(([slug, enabled]) => ({
                    slug,
                    enabled,
                  }))
                : []
            }
          />
          <BypassMechanism
            label="Blocklist HaGeZi DoH/VPN dans AdGuard"
            description="Filtre les endpoints DoH publics (Firefox Secure DNS, Chrome, Brave, etc.) et les VPN/proxies courants. Liste mise à jour quotidiennement, ~600 entrées. À activer depuis la page AdGuard > Filtres."
            active={null}
            note="Activable depuis la page AdGuard (feed slug : hagezi-doh-vpn)"
          />
        </div>

        {enableMut.isError && (
          <div className="mb-3 flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>
              {(enableMut.error as AxiosError<{ detail: string }>)?.response?.data?.detail ??
                (enableMut.error as Error)?.message}
            </span>
          </div>
        )}

        <div className="flex items-center justify-between gap-2 border-t border-[color:var(--color-cyber-border)] pt-3">
          <p className="text-xs text-[color:var(--color-cyber-dim)]">
            Le DoT du Slate vers ses résolveurs upstream n'est pas affecté
            (trafic OUTPUT, pas FORWARD).
          </p>
          {data?.any_active ? (
            <button
              onClick={() => {
                if (confirm("Désactiver l'anti-bypass DoT/DoH ?"))
                  disableMut.mutate();
              }}
              disabled={pending}
              className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-sm text-amber-300 hover:bg-amber-500/20 disabled:opacity-50"
            >
              {pending ? "Application…" : "Désactiver l'anti-bypass"}
            </button>
          ) : (
            <button
              onClick={() => enableMut.mutate()}
              disabled={pending}
              className="rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-sm text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
            >
              {pending ? "Application…" : "Activer l'anti-bypass"}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function BypassMechanism({
  label,
  description,
  active,
  subItems,
  note,
}: {
  label: string;
  description: string;
  active: boolean | null;
  subItems?: { slug: string; enabled: boolean | null }[];
  note?: string;
}) {
  return (
    <div className="rounded border border-[color:var(--color-cyber-border)] bg-slate-950/40 p-3">
      <div className="mb-1 flex items-center gap-2">
        {active === true ? (
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
        ) : active === false ? (
          <ShieldOff className="h-4 w-4 shrink-0 text-amber-400" />
        ) : (
          <Lock className="h-4 w-4 shrink-0 text-[color:var(--color-cyber-dim)]" />
        )}
        <span className="text-sm font-medium text-[color:var(--color-cyber-fg)]">{label}</span>
      </div>
      <p className="ml-6 text-xs text-[color:var(--color-cyber-muted)]">{description}</p>
      {note && (
        <p className="ml-6 mt-1 text-[11px] italic text-cyan-400">{note}</p>
      )}
      {subItems && subItems.length > 0 && (
        <div className="ml-6 mt-2 grid grid-cols-1 gap-x-3 gap-y-0.5 text-[11px] sm:grid-cols-2">
          {subItems.map((s) => (
            <span
              key={s.slug}
              className={`font-mono ${
                s.enabled === true
                  ? "text-emerald-400"
                  : s.enabled === false
                  ? "text-amber-400"
                  : "text-slate-600"
              }`}
            >
              {s.enabled === true ? "✓" : s.enabled === false ? "✗" : "—"}{" "}
              {s.slug}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
