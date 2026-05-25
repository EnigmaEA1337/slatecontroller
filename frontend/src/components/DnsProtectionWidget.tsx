import { useMemo, useState } from "react";
import { AxiosError } from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Flag,
  HeartHandshake,
  Shield,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  Trash2,
  Zap,
} from "lucide-react";

import {
  getDnsCatalog,
  getSecurityLevels,
  listProtections,
  removeProtection,
  setProtection,
} from "@/api/dns";
import type { DnsProvider, SecurityLevel } from "@/types/dns";
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

/**
 * Compact widget showing this network's DNS protection (level + provider
 * + DoT/DoH badges) with inline edit/remove actions. Self-contained: fetches
 * what it needs, opens its own configuration modal, invalidates the relevant
 * react-query keys on save/remove.
 *
 * Intended for embedding in a network card on the Networks page. The full
 * DNS catalog + level editor still lives at /protection/dns.
 */
export default function DnsProtectionWidget({
  networkSlug,
  networkName,
}: {
  networkSlug: string;
  networkName: string;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);

  const protections = useQuery({
    queryKey: ["dns", "protections"],
    queryFn: listProtections,
  });
  const levels = useQuery({
    queryKey: ["dns", "levels"],
    queryFn: getSecurityLevels,
    staleTime: 60_000,
  });
  const catalog = useQuery({
    queryKey: ["dns", "catalog"],
    queryFn: () => getDnsCatalog(),
    staleTime: 5 * 60_000,
  });

  const protection = useMemo(
    () =>
      (protections.data?.protections ?? []).find(
        (p) => p.network_slug === networkSlug,
      ),
    [protections.data, networkSlug],
  );

  const levelBySlug = useMemo(() => {
    const m = new Map<string, SecurityLevel>();
    for (const l of levels.data?.levels ?? []) m.set(l.slug, l);
    return m;
  }, [levels.data]);

  const removeMut = useMutation({
    mutationFn: () => removeProtection(networkSlug),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dns", "protections"] }),
  });

  // ---- Status row ----
  if (!protection) {
    return (
      <>
        <div className="mt-3 flex items-center justify-between gap-2 rounded border border-[color:var(--color-cyber-border)] bg-slate-900/40 px-3 py-2 text-xs">
          <span className="flex items-center gap-2 text-[color:var(--color-cyber-dim)]">
            <ShieldOff className="h-3.5 w-3.5" />
            Aucune protection DNS configurée
          </span>
          <button
            onClick={() => setEditing(true)}
            className="rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-0.5 text-[11px] text-cyan-300 hover:bg-cyan-500/20"
          >
            Configurer DNS
          </button>
        </div>
        {editing && (
          <EditModal
            networkSlug={networkSlug}
            networkName={networkName}
            currentLevelSlug={undefined}
            currentProviderSlug={undefined}
            levels={levels.data?.levels ?? []}
            providers={catalog.data?.providers ?? []}
            onClose={() => setEditing(false)}
            onSaved={() => {
              qc.invalidateQueries({ queryKey: ["dns", "protections"] });
              setEditing(false);
            }}
          />
        )}
      </>
    );
  }

  const level = levelBySlug.get(protection.level_slug);
  const color = level?.color ?? "#3b82f6";

  return (
    <>
      <div className="mt-3 rounded border border-[color:var(--color-cyber-border)] bg-slate-900/40 px-3 py-2 text-xs">
        <div className="flex items-start justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <LevelIcon
              name={level?.icon ?? "Shield"}
              className="h-3.5 w-3.5"
            />
            <span className="font-medium text-[color:var(--color-cyber-fg)]" style={{ color }}>
              {protection.level_name}
            </span>
            <span className="text-[color:var(--color-cyber-dim)]">via</span>
            <span className="text-[color:var(--color-cyber-fg)]">{protection.provider_name}</span>
            {protection.provider_eu_based && (
              <Flag className="h-3 w-3 text-blue-400" />
            )}
            {protection.upstream_transports.map((t) => (
              <span
                key={t}
                className={`rounded border px-1 py-0 text-[9px] font-mono uppercase ${
                  t === "DoT"
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    : t === "DoH"
                    ? "border-blue-500/40 bg-blue-500/10 text-blue-300"
                    : "border-amber-500/40 bg-amber-500/10 text-amber-300"
                }`}
              >
                {t}
              </span>
            ))}
          </div>
          <div className="flex shrink-0 gap-1">
            <button
              onClick={() => setEditing(true)}
              className="rounded border border-[color:var(--color-cyber-border-strong)] px-1.5 py-0.5 text-[10px] text-[color:var(--color-cyber-fg)] hover:border-cyan-500 hover:text-cyan-300"
            >
              Modifier
            </button>
            <button
              onClick={() => {
                if (confirm(`Retirer la protection DNS de ${networkName} ?`))
                  removeMut.mutate();
              }}
              disabled={removeMut.isPending}
              className="rounded border border-[color:var(--color-cyber-border-strong)] px-1.5 py-0.5 text-[10px] text-[color:var(--color-cyber-muted)] hover:border-red-500 hover:text-red-300 disabled:opacity-40"
              title="Supprimer (le client AdGuard est aussi supprimé)"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </div>
        </div>
      </div>

      {editing && (
        <EditModal
          networkSlug={networkSlug}
          networkName={networkName}
          currentLevelSlug={protection.level_slug}
          currentProviderSlug={protection.provider_slug}
          levels={levels.data?.levels ?? []}
          providers={catalog.data?.providers ?? []}
          onClose={() => setEditing(false)}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ["dns", "protections"] });
            setEditing(false);
          }}
        />
      )}
    </>
  );
}

/**
 * Modal where the user picks a level + (optional) provider override.
 * Same shape as the one on the /protection/dns page — kept here as a
 * minor duplication to keep this widget standalone-importable. If we
 * grow more entry points, factor out into a shared component.
 */
function EditModal({
  networkSlug,
  networkName,
  currentLevelSlug,
  currentProviderSlug,
  levels,
  providers,
  onClose,
  onSaved,
}: {
  networkSlug: string;
  networkName: string;
  currentLevelSlug: string | undefined;
  currentProviderSlug: string | undefined;
  levels: SecurityLevel[];
  providers: DnsProvider[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [levelSlug, setLevelSlug] = useState<string>(
    currentLevelSlug ?? levels[0]?.slug ?? "",
  );
  const selectedLevel = levels.find((l) => l.slug === levelSlug);
  const [providerSlug, setProviderSlug] = useState<string>(
    currentProviderSlug ?? selectedLevel?.default_provider_slug ?? "",
  );

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

  function handleLevelChange(slug: string) {
    setLevelSlug(slug);
    const lv = levels.find((l) => l.slug === slug);
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

  const selectedProvider = providers.find((p) => p.slug === providerSlug);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-lg border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-1 text-lg font-bold text-[color:var(--color-cyber-fg)]">
          Protection DNS — {networkName}
        </h2>
        <p className="mb-4 text-xs text-[color:var(--color-cyber-muted)]">
          Crée un client AdGuard pour ce réseau avec l'upstream chiffré
          (DoT/DoH) et les filtres du niveau choisi.
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
                  ? "bg-[color:var(--color-cyber-surface-2)]"
                  : "border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)]/40 hover:bg-[color:var(--color-cyber-surface-2)]"
              }`}
              style={
                levelSlug === lv.slug
                  ? {
                      borderColor: lv.color,
                      boxShadow: `0 0 0 2px ${lv.color}33`,
                    }
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
          {allowedProviders.length === 0 && (
            <option value="">— aucun provider compatible —</option>
          )}
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
              {(saveMut.error as AxiosError<{ detail: string }>)?.response?.data
                ?.detail ??
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
