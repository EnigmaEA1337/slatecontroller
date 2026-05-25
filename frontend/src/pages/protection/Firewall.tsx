/**
 * Protection → Firewall — read-only dump of every UCI section on the
 * Slate's firewall config. Consumes `/api/firewall` and shows the
 * snapshot grouped by origin (slate-ctrl / gl-inet / openwrt / user)
 * with a filter bar.
 *
 * V1 = view-only. Toggles and edits stay on their dedicated pages
 * (anti-bypass, agent apply, etc.) until we decide which mutations
 * are safe to expose here without footgun risk.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  Filter,
  Flame,
  RefreshCw,
  Shield,
  XCircle,
} from "lucide-react";

import { api } from "@/api/client";
import { errorMessage } from "@/lib/error-utils";

type Origin = "slate-ctrl" | "gl-inet" | "openwrt" | "user";

interface FwZone {
  name: string;
  section_id: string;
  input: string | null;
  output: string | null;
  forward: string | null;
  networks: string[];
  masq: boolean;
}

interface FwRule {
  section_id: string;
  name: string | null;
  src: string | null;
  dest: string | null;
  proto: string | string[] | null;
  src_port: string | null;
  dest_port: string | null;
  src_ip: string | null;
  dest_ip: string | null;
  target: string | null;
  family: string | null;
  enabled: boolean;
  origin: Origin;
  raw: Record<string, string>;
}

interface FwForwarding {
  section_id: string;
  name: string | null;
  src: string | null;
  dest: string | null;
  enabled: boolean;
  origin: Origin;
}

interface FwInclude {
  section_id: string;
  name: string | null;
  path: string | null;
}

interface FwDefaults {
  input: string | null;
  output: string | null;
  forward: string | null;
  syn_flood: boolean | null;
  drop_invalid: boolean | null;
}

interface Snapshot {
  defaults: FwDefaults | null;
  zones: FwZone[];
  rules: FwRule[];
  forwardings: FwForwarding[];
  includes: FwInclude[];
  counts: Record<string, number>;
}

async function fetchSnapshot(): Promise<Snapshot> {
  const { data } = await api.get<Snapshot>("/api/firewall");
  return data;
}

const ORIGIN_META: Record<
  Origin,
  { label: string; chip: string; description: string }
> = {
  "slate-ctrl": {
    label: "SC",
    chip: "border-[color:var(--color-cyber-accent)] text-[color:var(--color-cyber-accent)]",
    description: "Règles injectées par Slate Controller (préfixe SC_FR_)",
  },
  "gl-inet": {
    label: "GL",
    chip: "border-orange-400/60 text-orange-300",
    description: "Règles bundlées par le firmware GL.iNet (leak rules, etc.)",
  },
  openwrt: {
    label: "OW",
    chip: "border-sky-400/60 text-sky-300",
    description: "Règles stock OpenWrt (Allow-DHCP, Allow-IGMP, …)",
  },
  user: {
    label: "USR",
    chip: "border-[color:var(--color-cyber-border-strong)] text-[color:var(--color-cyber-muted)]",
    description: "Tout le reste — règles ajoutées hors-stack par l'utilisateur",
  },
};

function targetColor(target: string | null): string {
  switch ((target || "").toUpperCase()) {
    case "ACCEPT":
      return "text-emerald-300";
    case "DROP":
    case "REJECT":
      return "text-red-300";
    case "MASQUERADE":
    case "DNAT":
    case "SNAT":
      return "text-purple-300";
    default:
      return "text-[color:var(--color-cyber-muted)]";
  }
}

function protoLabel(p: string | string[] | null): string {
  if (!p) return "—";
  if (Array.isArray(p)) return p.join("/");
  return p;
}

export default function FirewallPage() {
  const q = useQuery({
    queryKey: ["firewall", "snapshot"],
    queryFn: fetchSnapshot,
    refetchInterval: 30_000,
  });

  const [filterOrigin, setFilterOrigin] = useState<Origin | "all">("all");
  const [showDisabled, setShowDisabled] = useState(true);
  const [search, setSearch] = useState("");

  const rules = q.data?.rules ?? [];

  const filtered = useMemo(() => {
    let r = rules;
    if (filterOrigin !== "all") {
      r = r.filter((x) => x.origin === filterOrigin);
    }
    if (!showDisabled) {
      r = r.filter((x) => x.enabled);
    }
    const q = search.trim().toLowerCase();
    if (q) {
      r = r.filter((x) =>
        [x.name, x.section_id, x.src, x.dest, x.dest_port, x.target]
          .filter(Boolean)
          .some((v) => String(v).toLowerCase().includes(q)),
      );
    }
    return r;
  }, [rules, filterOrigin, showDisabled, search]);

  return (
    <div className="mx-auto max-w-7xl px-6 py-10">
      <header className="mb-6">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Flame className="cyber-glow h-3 w-3" />
          protection · firewall
        </div>
        <h1 className="cyber-display cyber-glitch text-4xl" data-text="FIREWALL">
          FIREWALL
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          snapshot live des règles UCI sur le Slate · refresh 30 s · read-only
        </p>
      </header>

      {q.isError && (
        <p className="cyber-chip cyber-chip-on mb-4 block !rounded-none px-3 py-2 text-xs">
          {errorMessage(q.error)}
        </p>
      )}

      {/* Counts row */}
      {q.data && (
        <section className="cyber-card mb-4 grid grid-cols-2 gap-3 p-4 text-[11px] md:grid-cols-6">
          <Stat label="Zones" value={q.data.counts.zones} />
          <Stat
            label="Règles"
            value={`${q.data.counts.rules_enabled}/${q.data.counts.rules_total}`}
            sub="enabled / total"
          />
          <Stat
            label="SC"
            value={q.data.counts.rules_slate_ctrl}
            color="text-[color:var(--color-cyber-accent)]"
          />
          <Stat
            label="GL.iNet"
            value={q.data.counts.rules_gl_inet}
            color="text-orange-300"
          />
          <Stat
            label="OpenWrt"
            value={q.data.counts.rules_openwrt}
            color="text-sky-300"
          />
          <Stat
            label="Forwardings"
            value={q.data.counts.forwardings}
          />
        </section>
      )}

      {/* Zones */}
      {q.data && q.data.zones.length > 0 && (
        <section className="cyber-card mb-4 p-5">
          <header className="mb-3 flex items-center gap-2">
            <Shield className="cyber-glow h-3.5 w-3.5" />
            <h2 className="cyber-display cyber-glow text-sm">Zones</h2>
          </header>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
            {q.data.zones.map((z) => (
              <div
                key={z.section_id}
                className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)]/40 p-3 text-[11px]"
              >
                <div className="mb-1 flex items-baseline justify-between">
                  <span className="font-mono text-[color:var(--color-cyber-accent)]">
                    {z.name}
                  </span>
                  {z.masq && (
                    <span className="cyber-chip cyber-chip-warn !text-[9px]">
                      MASQ
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-1 text-[10px]">
                  <Policy label="in" value={z.input} />
                  <Policy label="out" value={z.output} />
                  <Policy label="fwd" value={z.forward} />
                </div>
                {z.networks.length > 0 && (
                  <div className="mt-2 text-[10px] text-[color:var(--color-cyber-muted)]">
                    {z.networks.join(", ")}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Filter bar */}
      <section className="cyber-card mb-3 flex flex-wrap items-center gap-3 p-3 text-[11px]">
        <Filter className="h-3 w-3 text-[color:var(--color-cyber-muted)]" />
        {(["all", "slate-ctrl", "gl-inet", "openwrt", "user"] as const).map(
          (k) => (
            <button
              key={k}
              type="button"
              onClick={() => setFilterOrigin(k)}
              className={`cyber-chip !text-[10px] ${
                filterOrigin === k
                  ? "border-[color:var(--color-cyber-accent)] text-[color:var(--color-cyber-fg)]"
                  : "text-[color:var(--color-cyber-muted)]"
              }`}
            >
              {k === "all" ? "TOUS" : ORIGIN_META[k as Origin].label}
            </button>
          ),
        )}
        <label className="flex items-center gap-1 text-[10px] text-[color:var(--color-cyber-muted)]">
          <input
            type="checkbox"
            checked={showDisabled}
            onChange={(e) => setShowDisabled(e.target.checked)}
            className="h-3 w-3"
          />
          inclure disabled
        </label>
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="recherche (nom, src, dest, port…)"
          className="ml-auto w-64 border border-[color:var(--color-cyber-border)] bg-transparent px-2 py-1 text-[11px] text-[color:var(--color-cyber-fg)] focus:border-[color:var(--color-cyber-accent)] focus:outline-none"
        />
        <button
          type="button"
          onClick={() => q.refetch()}
          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
        >
          <RefreshCw className={`h-3 w-3 ${q.isFetching ? "animate-spin" : ""}`} />
          refresh
        </button>
      </section>

      {/* Rules table */}
      <section className="cyber-card p-3">
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-left text-[9px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
                <th className="px-2 py-2">Origine</th>
                <th className="px-2 py-2">Nom / Section</th>
                <th className="px-2 py-2">Src</th>
                <th className="px-2 py-2">Dest</th>
                <th className="px-2 py-2">Proto</th>
                <th className="px-2 py-2">Port</th>
                <th className="px-2 py-2">Target</th>
                <th className="px-2 py-2 text-center">État</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td
                    colSpan={8}
                    className="px-2 py-6 text-center text-[color:var(--color-cyber-muted)]"
                  >
                    aucune règle ne match les filtres
                  </td>
                </tr>
              ) : (
                filtered.map((r) => (
                  <tr
                    key={r.section_id}
                    className={`border-t border-[color:var(--color-cyber-border)] ${
                      !r.enabled ? "opacity-50" : ""
                    }`}
                  >
                    <td className="px-2 py-2">
                      <span
                        className={`cyber-chip !text-[9px] ${ORIGIN_META[r.origin].chip}`}
                        title={ORIGIN_META[r.origin].description}
                      >
                        {ORIGIN_META[r.origin].label}
                      </span>
                    </td>
                    <td className="px-2 py-2">
                      <div className="font-mono text-[11px] text-[color:var(--color-cyber-fg)]">
                        {r.name || r.section_id}
                      </div>
                      {r.name && r.name !== r.section_id && (
                        <div className="font-mono text-[9px] text-[color:var(--color-cyber-muted)]">
                          {r.section_id}
                        </div>
                      )}
                    </td>
                    <td className="px-2 py-2 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                      {r.src || "—"}
                      {r.src_ip && <div className="text-[9px]">{r.src_ip}</div>}
                      {r.src_port && <div className="text-[9px]">:{r.src_port}</div>}
                    </td>
                    <td className="px-2 py-2 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                      {r.dest || "—"}
                      {r.dest_ip && <div className="text-[9px]">{r.dest_ip}</div>}
                    </td>
                    <td className="px-2 py-2 font-mono text-[10px]">
                      {protoLabel(r.proto)}
                    </td>
                    <td className="px-2 py-2 font-mono text-[10px]">
                      {r.dest_port || "—"}
                    </td>
                    <td
                      className={`px-2 py-2 font-mono text-[10px] font-bold ${targetColor(r.target)}`}
                    >
                      {r.target || "—"}
                    </td>
                    <td className="px-2 py-2 text-center">
                      {r.enabled ? (
                        <CheckCircle2 className="inline h-3.5 w-3.5 text-emerald-400" />
                      ) : (
                        <XCircle className="inline h-3.5 w-3.5 text-[color:var(--color-cyber-muted)]" />
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Forwardings + Includes (compact) */}
      {q.data && (q.data.forwardings.length > 0 || q.data.includes.length > 0) && (
        <section className="mt-4 grid gap-4 md:grid-cols-2">
          {q.data.forwardings.length > 0 && (
            <div className="cyber-card p-4">
              <h3 className="cyber-label mb-2 text-[10px]">forwardings</h3>
              <div className="space-y-1 text-[11px]">
                {q.data.forwardings.map((f) => (
                  <div
                    key={f.section_id}
                    className={`flex items-center gap-2 ${!f.enabled ? "opacity-50" : ""}`}
                  >
                    <span
                      className={`cyber-chip !text-[9px] ${ORIGIN_META[f.origin].chip}`}
                    >
                      {ORIGIN_META[f.origin].label}
                    </span>
                    <span className="font-mono">{f.src}</span>
                    <span className="text-[color:var(--color-cyber-muted)]">→</span>
                    <span className="font-mono">{f.dest}</span>
                    {f.enabled ? (
                      <CheckCircle2 className="ml-auto h-3 w-3 text-emerald-400" />
                    ) : (
                      <XCircle className="ml-auto h-3 w-3 text-[color:var(--color-cyber-muted)]" />
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          {q.data.includes.length > 0 && (
            <div className="cyber-card p-4">
              <h3 className="cyber-label mb-2 text-[10px]">includes</h3>
              <div className="space-y-1 text-[11px]">
                {q.data.includes.map((inc) => (
                  <div key={inc.section_id} className="flex items-baseline gap-2">
                    <span className="font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                      {inc.name || inc.section_id}
                    </span>
                    <span className="ml-auto font-mono text-[9px] text-[color:var(--color-cyber-muted)]">
                      {inc.path}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: number | string | undefined;
  sub?: string;
  color?: string;
}) {
  const display = value === undefined ? "—" : value;
  return (
    <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg-2)]/40 p-3">
      <div className="cyber-label text-[9px]">{label}</div>
      <div className={`cyber-display text-2xl ${color ?? "text-[color:var(--color-cyber-fg)]"}`}>
        {display}
      </div>
      {sub && (
        <div className="text-[9px] text-[color:var(--color-cyber-muted)]">{sub}</div>
      )}
    </div>
  );
}

function Policy({ label, value }: { label: string; value: string | null }) {
  const v = (value || "—").toUpperCase();
  const c =
    v === "ACCEPT"
      ? "text-emerald-300"
      : v === "DROP" || v === "REJECT"
        ? "text-red-300"
        : "text-[color:var(--color-cyber-muted)]";
  return (
    <div className="border-l border-[color:var(--color-cyber-border)] pl-2">
      <div className="text-[8px] uppercase tracking-wider text-[color:var(--color-cyber-muted)]">
        {label}
      </div>
      <div className={`font-mono ${c}`}>{v}</div>
    </div>
  );
}
