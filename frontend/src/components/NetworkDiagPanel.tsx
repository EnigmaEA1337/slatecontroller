/**
 * Live L2/L3 network diagnostic panel.
 *
 * Lazy-loaded section embedded in /networks. Fetches on demand because the
 * underlying SSH chain to the Slate takes ~25s; we don't want it to delay
 * the static "Network cards" view on every page entry.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Filter,
  Network as NetworkIcon,
  RefreshCw,
  Route,
  Wifi,
} from "lucide-react";
import { getNetworkDiag } from "@/api/networks";
import type {
  DiagInterface,
  DiagLogicalInterface,
  DiagNeighbour,
  DiagRoute,
  DiagRule,
} from "@/types/network-diag";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";


function fmtBytes(n: number | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KiB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MiB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GiB`;
}

const STATE_CLS: Record<string, string> = {
  UP: "border-emerald-500/60 text-emerald-300",
  DOWN: "border-red-500/60 text-red-300",
  UNKNOWN: "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]",
};

export default function NetworkDiagPanel() {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<
    "interfaces" | "routes" | "rules" | "neighbours" | "logical"
  >("interfaces");
  const [hideDown, setHideDown] = useState(true);
  const q = useQuery({
    queryKey: ["network-diag"],
    queryFn: getNetworkDiag,
    enabled: open, // lazy-load only when section opened
    staleTime: 30_000,
  });

  const ifaces = useMemo(() => {
    const list = q.data?.interfaces ?? [];
    return hideDown ? list.filter((i) => i.operstate === "UP") : list;
  }, [q.data, hideDown]);

  return (
    <section className="cyber-card cyber-card-accent mb-6 p-5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 text-left"
      >
        <Activity className="cyber-glow h-5 w-5" />
        <div className="flex-1">
          <div className="cyber-display cyber-glow text-lg">DIAGNOSTIC</div>
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
            L2/L3 live: interfaces · routes · ARP · interfaces UCI
          </div>
        </div>
        {open ? (
          <ChevronDown className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />
        ) : (
          <ChevronRight className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />
        )}
      </button>

      {open && (
        <div className="mt-4 space-y-3">
          {/* tabs */}
          <div className="flex flex-wrap items-center gap-2">
            {(
              [
                { id: "interfaces" as const, label: `Interfaces (${q.data?.interfaces.length ?? "…"})`, icon: Wifi },
                { id: "routes" as const, label: `Routes (${(q.data?.routes_v4.length ?? 0) + (q.data?.routes_v6.length ?? 0) || "…"})`, icon: Route },
                { id: "rules" as const, label: `Policy rules (${q.data?.rules?.length ?? "…"})`, icon: Filter },
                { id: "neighbours" as const, label: `Voisins ARP/NDP (${q.data?.neighbours.length ?? "…"})`, icon: NetworkIcon },
                { id: "logical" as const, label: `UCI interfaces (${q.data?.logical_interfaces.length ?? "…"})`, icon: NetworkIcon },
              ]
            ).map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={cn(
                  "inline-flex items-center gap-1 border px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition",
                  tab === t.id
                    ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 text-[color:var(--color-cyber-accent)]"
                    : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
                )}
              >
                <t.icon className="h-3 w-3" />
                {t.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => q.refetch()}
              disabled={q.isFetching}
              className="ml-auto inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-50"
            >
              <RefreshCw className={cn("h-3 w-3", q.isFetching && "animate-spin")} />
              {q.isFetching ? "scan ~25s…" : "refresh"}
            </button>
          </div>

          {q.isLoading && (
            <div className="cyber-panel p-3 text-[10px] text-[color:var(--color-cyber-muted)]">
              Probing Slate (ip + ubus) — ~25s à froid…
            </div>
          )}
          {q.isError && (
            <div className="cyber-panel border border-red-500/40 bg-red-500/5 p-3 text-[10px] text-red-300">
              {errorMessage(q.error)}
            </div>
          )}

          {q.data && tab === "interfaces" && (
            <>
              <label className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
                <input
                  type="checkbox"
                  checked={hideDown}
                  onChange={(e) => setHideDown(e.target.checked)}
                  className="accent-[color:var(--color-cyber-accent)]"
                />
                Cacher les interfaces DOWN
              </label>
              <InterfacesTable ifaces={ifaces} />
            </>
          )}
          {q.data && tab === "routes" && (
            <RoutesTables v4={q.data.routes_v4} v6={q.data.routes_v6} />
          )}
          {q.data && tab === "rules" && (
            <RulesTable rules={q.data.rules ?? []} />
          )}
          {q.data && tab === "neighbours" && (
            <NeighboursTable neighbours={q.data.neighbours} />
          )}
          {q.data && tab === "logical" && (
            <LogicalTable logical={q.data.logical_interfaces} />
          )}
        </div>
      )}
    </section>
  );
}

function InterfacesTable({ ifaces }: { ifaces: DiagInterface[] }) {
  if (ifaces.length === 0) {
    return (
      <div className="cyber-panel p-3 text-[10px] text-[color:var(--color-cyber-muted)]">
        Aucune interface.
      </div>
    );
  }
  return (
    <div className="cyber-panel overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="border-b border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]">
          <tr>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">État</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Interface</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">MAC</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">MTU</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Bridge</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Adresses</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">
              <ArrowDown className="inline h-3 w-3" /> RX
            </th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">
              <ArrowUp className="inline h-3 w-3" /> TX
            </th>
          </tr>
        </thead>
        <tbody>
          {ifaces.map((i) => (
            <tr
              key={i.name}
              className="border-b border-[color:var(--color-cyber-border)]/40"
            >
              <td className="px-3 py-2">
                <span
                  className={cn(
                    "inline-flex items-center border px-1.5 py-[1px] text-[10px] uppercase tracking-[0.18em]",
                    STATE_CLS[i.operstate] ?? STATE_CLS.UNKNOWN,
                  )}
                >
                  {i.operstate}
                </span>
              </td>
              <td className="px-3 py-2 font-mono">{i.name}</td>
              <td className="px-3 py-2 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                {i.mac}
              </td>
              <td className="px-3 py-2 font-mono">{i.mtu}</td>
              <td className="px-3 py-2 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                {i.master ?? "—"}
              </td>
              <td className="px-3 py-2 text-[10px]">
                {i.addresses.length > 0 ? (
                  <div className="space-y-0.5 font-mono">
                    {i.addresses.map((a, k) => (
                      <div
                        key={k}
                        className={cn(
                          a.family === "inet"
                            ? "text-[color:var(--color-cyber-fg)]"
                            : "text-[color:var(--color-cyber-muted)]",
                        )}
                      >
                        {a.local}/{a.prefixlen}
                      </div>
                    ))}
                  </div>
                ) : (
                  <span className="text-[color:var(--color-cyber-muted)]">—</span>
                )}
              </td>
              <td className="px-3 py-2 font-mono text-[10px]">
                {fmtBytes(i.counters?.rx_bytes)}
                {i.counters && i.counters.rx_drop > 0 && (
                  <span className="ml-1 text-red-300">
                    ⚠{i.counters.rx_drop}
                  </span>
                )}
              </td>
              <td className="px-3 py-2 font-mono text-[10px]">
                {fmtBytes(i.counters?.tx_bytes)}
                {i.counters && i.counters.tx_drop > 0 && (
                  <span className="ml-1 text-red-300">
                    ⚠{i.counters.tx_drop}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Ordering for routing-table groups. "main" first (most operationally useful),
// "local" second (loopback/broadcast — verbose but informative), then any
// other named/numbered tables sorted, with "unspec" last (catch-all).
function tableSortKey(name: string): [number, string] {
  if (name === "main") return [0, ""];
  if (name === "local") return [1, ""];
  if (name === "unspec") return [99, ""];
  return [2, name];
}

function tableBadgeClass(name: string): string {
  // Highlight non-main tables so they stand out at a glance.
  if (name === "main") return "border-emerald-500/40 text-emerald-300";
  if (name === "local") return "border-sky-500/40 text-sky-300";
  return "border-[color:var(--color-cyber-accent)]/50 text-[color:var(--color-cyber-accent)]";
}

function groupByTable(rows: DiagRoute[]): Map<string, DiagRoute[]> {
  const m = new Map<string, DiagRoute[]>();
  for (const r of rows) {
    const t = r.table || "main";
    if (!m.has(t)) m.set(t, []);
    m.get(t)!.push(r);
  }
  return new Map(
    [...m.entries()].sort((a, b) => {
      const [aPrio, aName] = tableSortKey(a[0]);
      const [bPrio, bName] = tableSortKey(b[0]);
      return aPrio - bPrio || aName.localeCompare(bName);
    }),
  );
}

function RoutesTables({ v4, v6 }: { v4: DiagRoute[]; v6: DiagRoute[] }) {
  const [showLocal, setShowLocal] = useState(false);
  // Hide the noisy `local` table by default — it's just broadcast and host
  // routes — but make it discoverable via a toggle.
  const filter = (rows: DiagRoute[]) =>
    showLocal ? rows : rows.filter((r) => r.table !== "local");

  const v4Filtered = filter(v4);
  const v6Filtered = filter(v6);
  const hiddenLocal = (v4.length - v4Filtered.length) + (v6.length - v6Filtered.length);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 text-[10px]">
        <label className="inline-flex items-center gap-1.5 uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          <input
            type="checkbox"
            checked={showLocal}
            onChange={(e) => setShowLocal(e.target.checked)}
            className="accent-[color:var(--color-cyber-accent)]"
          />
          Inclure la table <span className="font-mono">local</span>
        </label>
        {hiddenLocal > 0 && !showLocal && (
          <span className="text-[color:var(--color-cyber-muted)]">
            ({hiddenLocal} routes locales masquées)
          </span>
        )}
      </div>

      <div>
        <div className="cyber-label mb-1 text-[10px]">
          IPv4 ({v4Filtered.length}/{v4.length})
        </div>
        <RouteGroupedRows rows={v4Filtered} />
      </div>
      <div>
        <div className="cyber-label mb-1 text-[10px]">
          IPv6 ({v6Filtered.length}/{v6.length})
        </div>
        <RouteGroupedRows rows={v6Filtered} />
      </div>
    </div>
  );
}

function RouteGroupedRows({ rows }: { rows: DiagRoute[] }) {
  if (rows.length === 0)
    return (
      <div className="cyber-panel p-3 text-[10px] text-[color:var(--color-cyber-muted)]">
        Aucune route.
      </div>
    );

  const groups = groupByTable(rows);

  return (
    <div className="space-y-3">
      {[...groups.entries()].map(([table, tableRows]) => (
        <div key={table} className="cyber-panel overflow-x-auto">
          <div className="flex items-center gap-2 border-b border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-3 py-2">
            <span
              className={cn(
                "inline-flex items-center border px-2 py-[1px] text-[10px] font-bold uppercase tracking-[0.18em]",
                tableBadgeClass(table),
              )}
            >
              table {table}
            </span>
            <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
              {tableRows.length} route{tableRows.length > 1 ? "s" : ""}
            </span>
          </div>
          <table className="w-full text-xs">
            <thead className="border-b border-[color:var(--color-cyber-border)]/60">
              <tr>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">Destination</th>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">Via</th>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">Dev</th>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">Src</th>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">Métric</th>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">Proto</th>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">Type</th>
              </tr>
            </thead>
            <tbody>
              {tableRows.map((r, idx) => (
                <tr
                  key={idx}
                  className="border-b border-[color:var(--color-cyber-border)]/40"
                >
                  <td className="px-3 py-2 font-mono">{r.dst}</td>
                  <td className="px-3 py-2 font-mono">{r.gateway ?? "—"}</td>
                  <td className="px-3 py-2 font-mono">{r.dev}</td>
                  <td className="px-3 py-2 font-mono text-[color:var(--color-cyber-muted)]">
                    {r.src ?? "—"}
                  </td>
                  <td className="px-3 py-2 font-mono">{r.metric ?? "—"}</td>
                  <td className="px-3 py-2 font-mono text-[10px]">{r.protocol ?? "—"}</td>
                  <td className="px-3 py-2 font-mono text-[10px]">{r.type ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

function RulesTable({ rules }: { rules: DiagRule[] }) {
  if (rules.length === 0)
    return (
      <div className="cyber-panel p-3 text-[10px] text-[color:var(--color-cyber-muted)]">
        Aucune policy rule.
      </div>
    );
  return (
    <div className="cyber-panel overflow-x-auto">
      <div className="border-b border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-3 py-2 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
        Policy routing — quelle table consulter selon le paquet (priorité ↑ = évaluée en premier)
      </div>
      <table className="w-full text-xs">
        <thead className="border-b border-[color:var(--color-cyber-border)]/60">
          <tr>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Prio</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">From</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">To</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Iif</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Oif</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Fwmark</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">→ Table</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Action</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((r, idx) => (
            <tr
              key={idx}
              className="border-b border-[color:var(--color-cyber-border)]/40"
            >
              <td className="px-3 py-2 font-mono">{r.priority ?? "—"}</td>
              <td className="px-3 py-2 font-mono text-[10px]">{r.src ?? "all"}</td>
              <td className="px-3 py-2 font-mono text-[10px]">{r.dst ?? "—"}</td>
              <td className="px-3 py-2 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                {r.iif ?? "—"}
              </td>
              <td className="px-3 py-2 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                {r.oif ?? "—"}
              </td>
              <td className="px-3 py-2 font-mono text-[10px]">{r.fwmark ?? "—"}</td>
              <td className="px-3 py-2">
                <span
                  className={cn(
                    "inline-flex items-center border px-1.5 py-[1px] font-mono text-[10px] uppercase tracking-[0.18em]",
                    tableBadgeClass(r.table || "main"),
                  )}
                >
                  {r.table || "main"}
                </span>
              </td>
              <td className="px-3 py-2 font-mono text-[10px]">{r.action ?? "lookup"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const NEIGH_CLS: Record<string, string> = {
  REACHABLE: "text-emerald-300",
  STALE: "text-yellow-200",
  DELAY: "text-yellow-200",
  PROBE: "text-yellow-200",
  FAILED: "text-red-300",
  INCOMPLETE: "text-red-300",
};

function NeighboursTable({ neighbours }: { neighbours: DiagNeighbour[] }) {
  if (neighbours.length === 0)
    return (
      <div className="cyber-panel p-3 text-[10px] text-[color:var(--color-cyber-muted)]">
        Aucun voisin.
      </div>
    );
  return (
    <div className="cyber-panel overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="border-b border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]">
          <tr>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">IP</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">MAC</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Dev</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">État</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Rôle</th>
          </tr>
        </thead>
        <tbody>
          {neighbours.map((n, idx) => (
            <tr
              key={idx}
              className="border-b border-[color:var(--color-cyber-border)]/40"
            >
              <td className="px-3 py-2 font-mono">{n.ip}</td>
              <td className="px-3 py-2 font-mono text-[color:var(--color-cyber-muted)]">
                {n.lladdr ?? "—"}
              </td>
              <td className="px-3 py-2 font-mono">{n.dev}</td>
              <td
                className={cn(
                  "px-3 py-2 font-mono text-[10px]",
                  NEIGH_CLS[n.state ?? ""] ?? "text-[color:var(--color-cyber-muted)]",
                )}
              >
                {n.state ?? "—"}
              </td>
              <td className="px-3 py-2 text-[10px]">
                {n.router && (
                  <span className="border border-blue-500/40 px-1.5 py-[1px] uppercase tracking-[0.18em] text-blue-300">
                    router
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LogicalTable({ logical }: { logical: DiagLogicalInterface[] }) {
  if (logical.length === 0)
    return (
      <div className="cyber-panel p-3 text-[10px] text-[color:var(--color-cyber-muted)]">
        Aucune interface UCI.
      </div>
    );
  return (
    <div className="cyber-panel overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="border-b border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]">
          <tr>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Interface UCI</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Proto</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Up</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">L3 device</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">IPv4</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">DNS</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Uptime</th>
          </tr>
        </thead>
        <tbody>
          {logical.map((li) => {
            const v4s = (li["ipv4-address"] as { address: string; mask: number }[]) ?? [];
            const dns = (li["dns-server"] as string[]) ?? [];
            return (
              <tr
                key={li.interface}
                className="border-b border-[color:var(--color-cyber-border)]/40"
              >
                <td className="px-3 py-2 font-mono">{li.interface}</td>
                <td className="px-3 py-2 font-mono">{li.proto}</td>
                <td className="px-3 py-2">
                  <span
                    className={cn(
                      "inline-flex items-center border px-1.5 py-[1px] text-[10px] uppercase tracking-[0.18em]",
                      li.up ? STATE_CLS.UP : STATE_CLS.DOWN,
                    )}
                  >
                    {li.up ? "up" : "down"}
                  </span>
                </td>
                <td className="px-3 py-2 font-mono text-[color:var(--color-cyber-muted)]">
                  {li.l3_device ?? "—"}
                </td>
                <td className="px-3 py-2 font-mono text-[10px]">
                  {v4s.length > 0
                    ? v4s.map((a) => `${a.address}/${a.mask}`).join(", ")
                    : "—"}
                </td>
                <td className="px-3 py-2 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                  {dns.length > 0 ? dns.join(", ") : "—"}
                </td>
                <td className="px-3 py-2 font-mono text-[10px]">
                  {li.uptime ? `${Math.round(li.uptime / 3600)}h` : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
