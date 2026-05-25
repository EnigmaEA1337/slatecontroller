import { FormEvent, memo, useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  ExternalLink,
  Filter as FilterIcon,
  ListFilter,
  Plus,
  Power,
  RefreshCw,
  Shield,
  ShieldCheck,
  ShieldOff,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import {
  type FeedEntry,
  addAdGuardFilter,
  applyAdGuardFeeds,
  getAdGuardDnssec,
  getAdGuardFeedCatalog,
  getAdGuardStats,
  getAdGuardStatus,
  listAdGuardFilters,
  refreshAdGuardFilters,
  removeAdGuardFilter,
  setAdGuardDnssec,
  setAdGuardProtection,
  toggleAdGuard,
  toggleAdGuardFilter,
} from "@/api/adguard";
import type { AdGuardFilter, AdGuardStatus } from "@/types/adguard";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";


const PRESET_LISTS: { name: string; url: string }[] = [
  {
    name: "HaGeZi Multi PRO",
    url: "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.txt",
  },
  {
    name: "OISD Big",
    url: "https://big.oisd.nl/",
  },
  {
    name: "AdGuard DNS Filter",
    url: "https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt",
  },
  {
    name: "Steven Black unified",
    url: "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
  },
];

// ---------------------------- Status card ---------------------------- #

function StatusCard({
  status,
  onToggle,
  toggling,
  onProtection,
  protectionLoading,
  toggleErr,
}: {
  status: AdGuardStatus;
  onToggle: (enabled: boolean) => void;
  toggling: boolean;
  onProtection: (enabled: boolean) => void;
  protectionLoading: boolean;
  toggleErr: unknown;
}) {
  const running = status.init_running && status.web_ui_reachable;
  const protOn = status.protection_enabled === true;

  return (
    <section className="cyber-card cyber-card-accent p-6">
      <header className="mb-4 flex items-center gap-3">
        <div
          className={cn(
            "flex h-10 w-10 items-center justify-center border bg-[color:var(--color-cyber-accent)]/10",
            running
              ? "cyber-glow border-[color:var(--color-cyber-ok)] text-[color:var(--color-cyber-ok)]"
              : "border-[color:var(--color-cyber-muted)] text-[color:var(--color-cyber-muted)]",
          )}
        >
          {running ? (
            <Shield className="h-5 w-5" />
          ) : (
            <ShieldOff className="h-5 w-5" />
          )}
        </div>
        <div className="flex-1">
          <h2 className="cyber-display cyber-glow text-lg">ADGUARD HOME</h2>
          <p className="mt-0.5 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
            DNS-level ad/tracker blocking · slate-side
          </p>
        </div>
        <div className="flex items-center gap-2">
          {running ? (
            <span className="cyber-chip cyber-chip-ok">running</span>
          ) : status.uci_enabled ? (
            <span className="cyber-chip cyber-chip-warn">UCI on · daemon down</span>
          ) : (
            <span className="cyber-chip">stopped</span>
          )}
        </div>
      </header>

      <div className="cyber-hatch mb-4 h-px w-full" />

      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3">
          <div className="cyber-label mb-1">UCI</div>
          <div
            className={cn(
              "font-mono text-sm font-extrabold",
              status.uci_enabled
                ? "cyber-glow"
                : "text-[color:var(--color-cyber-muted)]",
            )}
          >
            {status.uci_enabled ? "ON" : "off"}
          </div>
        </div>
        <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3">
          <div className="cyber-label mb-1">init.d</div>
          <div
            className={cn(
              "font-mono text-sm font-extrabold",
              status.init_running
                ? "cyber-glow"
                : "text-[color:var(--color-cyber-muted)]",
            )}
          >
            {status.init_running ? "running" : "stopped"}
          </div>
        </div>
        <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3">
          <div className="cyber-label mb-1">REST :{status.http_port}</div>
          <div
            className={cn(
              "font-mono text-sm font-extrabold",
              status.web_ui_reachable
                ? "cyber-glow"
                : "text-[color:var(--color-cyber-muted)]",
            )}
          >
            {status.web_ui_reachable ? "reachable" : "—"}
          </div>
        </div>
        <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3">
          <div className="cyber-label mb-1">protection</div>
          <div
            className={cn(
              "font-mono text-sm font-extrabold",
              protOn ? "cyber-glow" : "text-[color:var(--color-cyber-muted)]",
            )}
          >
            {status.protection_enabled === null
              ? "—"
              : protOn
                ? "ON"
                : "off"}
          </div>
        </div>
      </div>

      {(status.version || status.dns_port) && (
        <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-[color:var(--color-cyber-dim)]">
          {status.version && (
            <span>
              version <span className="cyber-glow-soft font-mono">{status.version}</span>
            </span>
          )}
          {status.dns_port && (
            <span>
              dns port{" "}
              <span className="cyber-glow-soft font-mono">{status.dns_port}</span>
            </span>
          )}
        </div>
      )}

      {status.error && (
        <p className="mt-3 border border-[color:var(--color-cyber-warn)] bg-[color:var(--color-cyber-warn)]/8 px-3 py-2 text-[11px]">
          <AlertTriangle className="mr-1.5 inline h-3 w-3" />
          {status.error}
        </p>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-2">
        {!status.uci_enabled ? (
          <button
            type="button"
            disabled={toggling}
            onClick={() => onToggle(true)}
            className="cyber-button inline-flex items-center gap-2 px-4 py-2.5 text-xs disabled:opacity-50"
          >
            <Power className="h-3.5 w-3.5" />
            {toggling ? "démarrage…" : "Activer AdGuard"}
          </button>
        ) : (
          <button
            type="button"
            disabled={toggling}
            onClick={() => onToggle(false)}
            className="inline-flex items-center gap-2 border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] px-4 py-2.5 text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-50"
          >
            <Power className="h-3.5 w-3.5" />
            {toggling ? "arrêt…" : "Désactiver"}
          </button>
        )}

        {status.web_ui_reachable && status.protection_enabled !== null && (
          <button
            type="button"
            disabled={protectionLoading}
            onClick={() => onProtection(!protOn)}
            className="inline-flex items-center gap-2 border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] px-3 py-2 text-[11px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-50"
          >
            {protOn ? "désactiver protection" : "activer protection"}
          </button>
        )}

        {status.web_ui_reachable && (
          <a
            href={status.web_ui_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 border border-transparent px-3 py-2 text-[11px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            <ExternalLink className="h-3 w-3" />
            web ui
          </a>
        )}
      </div>

      {toggleErr != null && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(toggleErr)}
        </p>
      )}
    </section>
  );
}

// ---------------------------- Stats ---------------------------- #

function StatsCard() {
  const query = useQuery({
    queryKey: ["adguard", "stats"],
    queryFn: getAdGuardStats,
    refetchInterval: 30_000,
    retry: false,
  });

  if (query.isError) {
    return (
      <section className="cyber-card p-5">
        <header className="cyber-label mb-2 flex items-center gap-2">
          <BarChart3 className="cyber-glow h-3 w-3" />
          dns stats
        </header>
        <p className="text-xs text-[color:var(--color-cyber-muted)]">
          Stats indisponibles (AdGuard inactif ou REST API muette)
        </p>
      </section>
    );
  }

  if (!query.data) {
    return (
      <section className="cyber-card p-5">
        <p className="cyber-label cyber-cursor">chargement stats</p>
      </section>
    );
  }

  const s = query.data;
  const blockedPct =
    s.num_dns_queries > 0
      ? Math.round((s.num_blocked_filtering / s.num_dns_queries) * 100)
      : 0;

  return (
    <section className="cyber-card p-5">
      <header className="mb-3 flex items-center gap-2">
        <BarChart3 className="cyber-glow h-3.5 w-3.5" />
        <span className="cyber-label">dns stats · 24h</span>
        <span className="ml-auto text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-dim)]">
          {s.avg_processing_time_ms.toFixed(1)} ms avg
        </span>
      </header>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3">
          <div className="cyber-label mb-1">queries</div>
          <div className="cyber-glow font-mono text-xl tabular-nums font-extrabold">
            {s.num_dns_queries.toLocaleString("fr-FR")}
          </div>
        </div>
        <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3">
          <div className="cyber-label mb-1">blocked</div>
          <div className="font-mono text-xl tabular-nums font-extrabold text-[color:var(--color-cyber-ok)]">
            {s.num_blocked_filtering.toLocaleString("fr-FR")}
          </div>
          <div className="mt-0.5 text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-dim)]">
            {blockedPct}% du trafic
          </div>
        </div>
        <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3">
          <div className="cyber-label mb-1">safebrowsing</div>
          <div className="font-mono text-xl tabular-nums font-extrabold text-[color:var(--color-cyber-muted)]">
            {s.num_replaced_safebrowsing.toLocaleString("fr-FR")}
          </div>
        </div>
        <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3">
          <div className="cyber-label mb-1">parental</div>
          <div className="font-mono text-xl tabular-nums font-extrabold text-[color:var(--color-cyber-muted)]">
            {s.num_replaced_parental.toLocaleString("fr-FR")}
          </div>
        </div>
      </div>

      {(s.top_blocked_domains.length > 0 || s.top_queried_domains.length > 0) && (
        <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-2">
          {s.top_blocked_domains.length > 0 && (
            <div>
              <div className="cyber-label mb-2 flex items-center gap-1.5">
                <Activity className="h-3 w-3 text-[color:var(--color-cyber-ok)]" />
                top blocked
              </div>
              <ul className="space-y-0.5 text-[11px] font-mono">
                {s.top_blocked_domains.slice(0, 8).map((d, i) => {
                  const [domain, count] = Object.entries(d)[0] ?? ["", 0];
                  return (
                    <li
                      key={`b-${i}`}
                      className="flex items-baseline justify-between gap-2 border-b border-[color:var(--color-cyber-border)] py-0.5"
                    >
                      <span className="truncate">{domain}</span>
                      <span className="shrink-0 text-[color:var(--color-cyber-ok)]">
                        {count}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {s.top_queried_domains.length > 0 && (
            <div>
              <div className="cyber-label mb-2 flex items-center gap-1.5">
                <Activity className="h-3 w-3" />
                top queried
              </div>
              <ul className="space-y-0.5 text-[11px] font-mono">
                {s.top_queried_domains.slice(0, 8).map((d, i) => {
                  const [domain, count] = Object.entries(d)[0] ?? ["", 0];
                  return (
                    <li
                      key={`q-${i}`}
                      className="flex items-baseline justify-between gap-2 border-b border-[color:var(--color-cyber-border)] py-0.5"
                    >
                      <span className="truncate">{domain}</span>
                      <span className="shrink-0 text-[color:var(--color-cyber-muted)]">
                        {count}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------- Filters ---------------------------- #

function AddFilterForm({
  onClose,
  existingUrls,
}: {
  onClose: () => void;
  existingUrls: Set<string>;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");

  const add = useMutation({
    mutationFn: () => addAdGuardFilter({ name, url }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["adguard", "filters"] });
      onClose();
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    add.mutate();
  }

  function pickPreset(p: { name: string; url: string }) {
    setName(p.name);
    setUrl(p.url);
  }

  return (
    <form
      onSubmit={submit}
      className="cyber-card cyber-card-accent space-y-3 p-5"
    >
      <div className="flex items-center justify-between">
        <h3 className="cyber-display cyber-glow text-base">NEW BLOCKLIST</h3>
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
          <span className="cyber-label mb-1.5 block">name</span>
          <input
            type="text"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="HaGeZi Multi PRO"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
        <label className="block">
          <span className="cyber-label mb-1.5 block">url</span>
          <input
            type="url"
            required
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
      </div>

      <div>
        <span className="cyber-label mb-1.5 block">presets</span>
        <div className="flex flex-wrap gap-1.5">
          {PRESET_LISTS.map((p) => {
            const already = existingUrls.has(p.url);
            return (
              <button
                key={p.url}
                type="button"
                disabled={already}
                onClick={() => pickPreset(p)}
                className={cn(
                  "border px-2 py-1 text-[10px] uppercase tracking-[0.15em]",
                  already
                    ? "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-dim)]"
                    : "border-[color:var(--color-cyber-border-strong)] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]",
                )}
                title={p.url}
              >
                {p.name}
                {already && " · déjà"}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex gap-2 pt-2">
        <button
          type="submit"
          disabled={add.isPending || !name || !url}
          className="cyber-button px-4 py-2 text-xs disabled:opacity-50"
        >
          {add.isPending ? "ajout…" : "Ajouter"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="border border-[color:var(--color-cyber-border-strong)] px-4 py-2 text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
        >
          Annuler
        </button>
      </div>

      {add.error && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(add.error)}
        </p>
      )}
    </form>
  );
}

// Memoised: rendered in a .map over every blocklist (10-30 filters typical,
// up to 100+ if the user has imported a full HaGeZi suite). Polling on the
// parent shouldn't re-render rows that didn't change.
const FilterRow = memo(function FilterRow({ filter }: { filter: AdGuardFilter }) {
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: () =>
      toggleAdGuardFilter({ url: filter.url, enabled: !filter.enabled }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["adguard", "filters"] }),
  });
  const remove = useMutation({
    mutationFn: () => removeAdGuardFilter(filter.url),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["adguard", "filters"] }),
  });

  return (
    <li className="flex items-start gap-3 border border-[color:var(--color-cyber-border)] p-3">
      <div
        className={cn(
          "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center border",
          filter.enabled
            ? "border-[color:var(--color-cyber-ok)] bg-[color:var(--color-cyber-ok)]/15 text-[color:var(--color-cyber-ok)]"
            : "border-[color:var(--color-cyber-muted)] text-[color:var(--color-cyber-muted)]",
        )}
      >
        {filter.enabled ? <CheckCircle2 className="h-3 w-3" /> : null}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <span
            className={cn(
              "text-sm",
              filter.enabled
                ? "text-[color:var(--color-cyber-fg)]"
                : "text-[color:var(--color-cyber-muted)]",
            )}
          >
            {filter.name || `filter #${filter.id}`}
          </span>
          <span className="cyber-chip">{filter.rules_count.toLocaleString("fr-FR")} rules</span>
          {filter.last_updated && (
            <span className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-dim)]">
              maj {new Date(filter.last_updated).toLocaleDateString("fr-FR")}
            </span>
          )}
        </div>
        <p className="mt-0.5 truncate font-mono text-[10px] text-[color:var(--color-cyber-dim)]">
          {filter.url}
        </p>
      </div>
      <div className="flex shrink-0 gap-1">
        <button
          type="button"
          disabled={toggle.isPending}
          onClick={() => toggle.mutate()}
          className="border border-transparent px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-40"
        >
          {filter.enabled ? "off" : "on"}
        </button>
        <button
          type="button"
          disabled={remove.isPending}
          onClick={() => {
            if (confirm(`Supprimer la blocklist "${filter.name}" ?`)) remove.mutate();
          }}
          className="border border-transparent p-1.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-40"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
    </li>
  );
});

function FiltersCard({ available }: { available: boolean }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);

  const query = useQuery({
    queryKey: ["adguard", "filters"],
    queryFn: listAdGuardFilters,
    enabled: available,
    retry: false,
  });

  const refresh = useMutation({
    mutationFn: refreshAdGuardFilters,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["adguard", "filters"] }),
  });

  if (!available) {
    return (
      <section className="cyber-card p-5">
        <header className="cyber-label mb-2 flex items-center gap-2">
          <ListFilter className="cyber-glow h-3 w-3" />
          blocklists
        </header>
        <p className="text-xs text-[color:var(--color-cyber-muted)]">
          AdGuard doit être actif pour gérer les blocklists.
        </p>
      </section>
    );
  }

  const existingUrls = new Set((query.data ?? []).map((f) => f.url));

  return (
    <section className="cyber-card p-5">
      <header className="mb-3 flex items-center gap-2">
        <ListFilter className="cyber-glow h-3.5 w-3.5" />
        <span className="cyber-label">
          blocklists · {query.data?.length ?? 0} configurées
        </span>
        <div className="ml-auto flex gap-1">
          <button
            type="button"
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
            className="inline-flex items-center gap-1.5 border border-transparent px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-50"
          >
            <RefreshCw
              className={cn("h-3 w-3", refresh.isPending && "animate-spin")}
            />
            {refresh.isPending ? "maj…" : "refresh"}
          </button>
          {!adding && (
            <button
              type="button"
              onClick={() => setAdding(true)}
              className="cyber-button inline-flex items-center gap-1.5 px-3 py-1 text-[10px]"
            >
              <Plus className="h-3 w-3" />
              Ajouter
            </button>
          )}
        </div>
      </header>

      {adding && (
        <div className="mb-4">
          <AddFilterForm
            onClose={() => setAdding(false)}
            existingUrls={existingUrls}
          />
        </div>
      )}

      {query.isLoading && <p className="cyber-label cyber-cursor">chargement</p>}
      {query.isError && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(query.error)}
        </p>
      )}

      {query.data && query.data.length === 0 && (
        <p className="text-xs text-[color:var(--color-cyber-muted)]">
          Aucune blocklist configurée. Clique "Ajouter" pour démarrer.
        </p>
      )}

      {query.data && query.data.length > 0 && (
        <ul className="space-y-2">
          {query.data.map((f) => (
            <FilterRow key={f.id} filter={f} />
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------- Feeds catalog ---------------------------- #

const INTENSITY_COLOR: Record<FeedEntry["intensity"], string> = {
  light: "cyber-chip-ok",
  balanced: "",
  pro: "cyber-chip-warn",
  hard: "cyber-chip-on",
};

// Memoised: rendered once per feed in the 16-entry catalog (+ user adds).
// `onToggle` receives the slug so the parent can pass ONE stable callback
// (via useCallback) for all feeds — otherwise we'd have to memoise N
// per-feed closures separately.
const FeedCard = memo(function FeedCard({
  feed,
  selected,
  onToggle,
}: {
  feed: FeedEntry;
  selected: boolean;
  onToggle: (slug: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onToggle(feed.slug)}
      className={cn(
        "block w-full border p-3 text-left transition-all",
        selected
          ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/8"
          : feed.active
            ? "border-[color:var(--color-cyber-ok)] bg-[color:var(--color-cyber-ok)]/5"
            : "border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] hover:border-[color:var(--color-cyber-border-strong)]",
      )}
    >
      <div className="flex items-start gap-2">
        <div
          className={cn(
            "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center border",
            selected
              ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/30"
              : feed.active
                ? "border-[color:var(--color-cyber-ok)] bg-[color:var(--color-cyber-ok)]/30"
                : "border-[color:var(--color-cyber-muted)]",
          )}
        >
          {(selected || feed.active) && <CheckCircle2 className="h-3 w-3" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-sm font-bold">{feed.name}</span>
            <span className={cn("cyber-chip", INTENSITY_COLOR[feed.intensity])}>
              {feed.intensity}
            </span>
            <span className="cyber-chip">{feed.category}</span>
            {feed.recommended && (
              <span className="cyber-chip cyber-chip-ok inline-flex items-center gap-1">
                <Sparkles className="h-2.5 w-2.5" />
                reco
              </span>
            )}
            {feed.active && (
              <span className="cyber-chip cyber-chip-ok">actif</span>
            )}
          </div>
          <p className="mt-0.5 text-[11px] text-[color:var(--color-cyber-muted)]">
            {feed.description}
          </p>
          <p className="mt-0.5 text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-dim)]">
            {feed.maintainer} ·{" "}
            <span className="font-mono normal-case tracking-normal">
              {feed.url}
            </span>
          </p>
        </div>
      </div>
    </button>
  );
});

function FeedsCatalogCard({ available }: { available: boolean }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [showAll, setShowAll] = useState(false);

  const query = useQuery({
    queryKey: ["adguard", "feeds-catalog"],
    queryFn: getAdGuardFeedCatalog,
    enabled: available,
    retry: false,
    refetchInterval: 30_000,
  });

  const apply = useMutation({
    mutationFn: (slugs: string[]) => applyAdGuardFeeds(slugs),
    onSuccess: () => {
      setSelected(new Set());
      queryClient.invalidateQueries({ queryKey: ["adguard"] });
    },
  });

  if (!available) {
    return (
      <section className="cyber-card p-5">
        <header className="cyber-label mb-2 flex items-center gap-2">
          <Sparkles className="cyber-glow h-3 w-3" />
          feeds catalog
        </header>
        <p className="text-xs text-[color:var(--color-cyber-muted)]">
          AdGuard doit être actif pour appliquer des feeds.
        </p>
      </section>
    );
  }

  // Stable across renders so memoised FeedCards don't re-render when their
  // sibling toggles. The functional setter avoids `selected` in the deps.
  const toggleSelect = useCallback(
    (slug: string) =>
      setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(slug)) next.delete(slug);
        else next.add(slug);
        return next;
      }),
    [],
  );

  const recommended = (query.data ?? []).filter((f) => f.recommended);
  const others = (query.data ?? []).filter((f) => !f.recommended);

  return (
    <section className="cyber-card p-5">
      <header className="mb-3 flex items-center gap-2">
        <Sparkles className="cyber-glow h-3.5 w-3.5" />
        <span className="cyber-label">
          feeds catalog · {query.data?.length ?? 0} curated
        </span>
        <div className="ml-auto flex gap-1">
          {selected.size > 0 && (
            <button
              type="button"
              disabled={apply.isPending}
              onClick={() => apply.mutate(Array.from(selected))}
              className="cyber-button inline-flex items-center gap-1.5 px-3 py-1 text-[10px] disabled:opacity-50"
            >
              <Plus className="h-3 w-3" />
              {apply.isPending
                ? "application…"
                : `Appliquer ${selected.size} feed${selected.size > 1 ? "s" : ""}`}
            </button>
          )}
        </div>
      </header>

      {query.isLoading && <p className="cyber-label cyber-cursor">chargement</p>}
      {query.isError && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(query.error)}
        </p>
      )}

      {recommended.length > 0 && (
        <>
          <div className="cyber-label mb-1.5 text-[10px]">recommandés</div>
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
            {recommended.map((f) => (
              <FeedCard
                key={f.slug}
                feed={f}
                selected={selected.has(f.slug)}
                onToggle={toggleSelect}
              />
            ))}
          </div>
        </>
      )}

      {others.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setShowAll((s) => !s)}
            className="mt-3 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-accent)]"
          >
            {showAll ? "▾" : "▸"} autres feeds ({others.length})
          </button>
          {showAll && (
            <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-2">
              {others.map((f) => (
                <FeedCard
                  key={f.slug}
                  feed={f}
                  selected={selected.has(f.slug)}
                  onToggle={toggleSelect}
                />
              ))}
            </div>
          )}
        </>
      )}

      {apply.error && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(apply.error)}
        </p>
      )}
    </section>
  );
}

// ---------------------------- DNSSEC card ---------------------------- #

/**
 * DNSSEC validation toggle. Lives on the AdGuard page (not per-network)
 * because it's a global AdGuard setting — every query that goes through
 * the resolver is validated, regardless of which client made it.
 *
 * Why it matters (kept short on the card; full pedagogy in /protection
 * threat-model modal):
 *  - without DNSSEC, AdGuard trusts whatever the upstream replies
 *  - a BGP hijack of Quad9/Cloudflare/DNS4EU silently poisons answers
 *  - a Kaminsky-style cache attack on the upstream goes undetected
 *  - ~0.5 % of zones have broken DNSSEC → they SERVFAIL (acceptable)
 */
function DnssecCard({ available }: { available: boolean }) {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ["adguard", "dnssec"],
    queryFn: getAdGuardDnssec,
    enabled: available,
    retry: false,
    refetchInterval: 30_000,
  });

  const mutation = useMutation({
    mutationFn: (enabled: boolean) => setAdGuardDnssec(enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["adguard"] });
      queryClient.invalidateQueries({ queryKey: ["hardening"] });
    },
  });

  if (!available) {
    return (
      <section className="cyber-card p-5">
        <header className="cyber-label mb-2 flex items-center gap-2">
          <ShieldCheck className="cyber-glow h-3 w-3" />
          dnssec validation
        </header>
        <p className="text-xs text-[color:var(--color-cyber-muted)]">
          AdGuard doit être actif pour configurer DNSSEC.
        </p>
      </section>
    );
  }

  const enabled = query.data?.enabled ?? false;
  const upstreams = query.data?.upstream_dns ?? [];

  return (
    <section className="cyber-card cyber-card-accent p-5">
      <header className="mb-3 flex items-center gap-2">
        <ShieldCheck className="cyber-glow h-3.5 w-3.5" />
        <span className="cyber-label">validation dnssec · global</span>
        {enabled ? (
          <span className="cyber-chip cyber-chip-ok ml-2">active</span>
        ) : (
          <span className="cyber-chip cyber-chip-on ml-2">inactive</span>
        )}
        <button
          type="button"
          disabled={mutation.isPending || query.isLoading}
          onClick={() => mutation.mutate(!enabled)}
          className="ml-auto cyber-button inline-flex items-center gap-1.5 px-3 py-1 text-[10px] disabled:opacity-50"
        >
          <Power className="h-3 w-3" />
          {mutation.isPending
            ? "…"
            : enabled
              ? "désactiver"
              : "activer"}
        </button>
      </header>

      <p className="mb-2 text-[11px] leading-relaxed text-[color:var(--color-cyber-muted)]">
        Vérifie localement les signatures RRSIG des réponses DNS au lieu de
        faire confiance aveuglément à l'upstream. Coupe les attaques de
        cache-poisoning (Kaminsky) et les BGP-hijacks de resolver public
        (ex. Rostelecom → AWS Route 53, avril 2020).
      </p>
      <p className="mb-2 text-[10px] leading-relaxed text-[color:var(--color-cyber-muted)]">
        <span className="text-[color:var(--color-cyber-warn)]">Trade-off :</span>{" "}
        ≈ 0,5 % des domaines ont une chaîne DNSSEC cassée côté propriétaire
        → ils retournent SERVFAIL. Acceptable pour la posture du Slate.
      </p>

      {upstreams.length > 0 && (
        <div className="mt-3 border-t border-[color:var(--color-cyber-border)] pt-2">
          <div className="cyber-label mb-1 text-[10px]">upstreams configurés</div>
          <ul className="space-y-0.5 text-[10px] text-[color:var(--color-cyber-muted)]">
            {upstreams.slice(0, 4).map((u, i) => (
              <li key={i} className="truncate">
                {u}
              </li>
            ))}
            {upstreams.length > 4 && (
              <li>… +{upstreams.length - 4} autres</li>
            )}
          </ul>
        </div>
      )}

      {mutation.error && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(mutation.error)}
        </p>
      )}
    </section>
  );
}


// ---------------------------- Page ---------------------------- #

export default function AdGuardPage() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["adguard", "status"],
    queryFn: getAdGuardStatus,
    refetchInterval: 15_000,
  });

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => toggleAdGuard(enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["adguard"] });
    },
  });

  const protection = useMutation({
    mutationFn: (enabled: boolean) => setAdGuardProtection(enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["adguard"] });
    },
  });

  const data = status.data;
  const isUp = data?.web_ui_reachable ?? false;

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <FilterIcon className="cyber-glow h-3 w-3" />
          protection · adguard home
        </div>
        <h1 className="cyber-display cyber-glitch text-4xl" data-text="ADGUARD">
          ADGUARD
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          dns filtering · blocklists · runtime stats
        </p>
      </header>

      {status.isLoading && (
        <p className="cyber-label cyber-cursor">chargement</p>
      )}

      {status.isError && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(status.error)}
        </p>
      )}

      {data && (
        <div className="space-y-5">
          <StatusCard
            status={data}
            onToggle={(enabled) => toggle.mutate(enabled)}
            toggling={toggle.isPending}
            onProtection={(enabled) => protection.mutate(enabled)}
            protectionLoading={protection.isPending}
            toggleErr={toggle.error ?? protection.error}
          />
          {isUp && <StatsCard />}
          <DnssecCard available={isUp} />
          <FeedsCatalogCard available={isUp} />
          <FiltersCard available={isUp} />
        </div>
      )}
    </div>
  );
}
