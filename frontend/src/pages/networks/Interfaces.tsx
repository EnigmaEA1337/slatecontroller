/**
 * Network Interfaces — read-only snapshot of every layer-2 / layer-3
 * interface on the Slate. Reuses the already-built `/api/networks/diag`
 * endpoint (which runs `ip -j addr/link`, parses /proc/net/dev counters,
 * and merges in the OpenWrt ubus interface map) — no new backend
 * surface needed.
 *
 * Layout : sortable table grouped by interface kind (bridges first,
 * then physical/wifi, then virtual). Each row shows operstate, MTU,
 * MAC, IPs (v4+v6), master bridge, and TX/RX bytes formatted human-
 * readably.
 */

import { useQuery } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Cable,
  Cpu,
  RefreshCw,
  Wifi as WifiIcon,
  Workflow,
} from "lucide-react";

import { api } from "@/api/client";
import { errorMessage } from "@/lib/error-utils";

interface DiagAddress {
  family: "inet" | "inet6";
  local: string;
  prefixlen: number;
  scope: string;
  broadcast?: string | null;
  label?: string | null;
}

interface DiagInterface {
  name: string;
  index: number | null;
  operstate: string | null;
  flags: string[];
  mtu: number | null;
  mac: string | null;
  master: string | null;
  link_type: string | null;
  addresses: DiagAddress[];
  counters: {
    rx_bytes?: number;
    tx_bytes?: number;
    rx_packets?: number;
    tx_packets?: number;
    rx_errors?: number;
    tx_errors?: number;
  } | null;
}

interface DiagResponse {
  interfaces: DiagInterface[];
  // routes, rules, neighbours, logical_interfaces also present but
  // unused on this page.
}

async function fetchDiag(): Promise<DiagResponse> {
  const { data } = await api.get<DiagResponse>("/api/networks/diag");
  return data;
}

function humanBytes(n: number | undefined): string {
  if (n === undefined || n === null) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
}

function classifyKind(iface: DiagInterface): "bridge" | "wifi" | "phy" | "virtual" {
  const n = iface.name;
  if (n.startsWith("br-")) return "bridge";
  if (
    n.startsWith("ra") ||
    n.startsWith("apcli") ||
    n.startsWith("wlan") ||
    n.startsWith("mt7990")
  )
    return "wifi";
  if (
    n.startsWith("eth") ||
    n === "lan" ||
    n === "wan" ||
    n.startsWith("usb")
  )
    return "phy";
  return "virtual";
}

function KindBadge({ kind }: { kind: ReturnType<typeof classifyKind> }) {
  const map = {
    bridge: { label: "BRIDGE", color: "text-[color:var(--color-cyber-accent)]", Icon: Workflow },
    wifi: { label: "WIFI", color: "text-emerald-300", Icon: WifiIcon },
    phy: { label: "ETH", color: "text-sky-300", Icon: Cable },
    virtual: { label: "VIRT", color: "text-[color:var(--color-cyber-muted)]", Icon: Cpu },
  } as const;
  const m = map[kind];
  return (
    <span className={`cyber-chip inline-flex items-center gap-1 !text-[9px] ${m.color}`}>
      <m.Icon className="h-2.5 w-2.5" />
      {m.label}
    </span>
  );
}

function OperBadge({ state }: { state: string | null }) {
  const up = state === "UP";
  const unknown = !state || state === "UNKNOWN";
  return (
    <span
      className={`cyber-chip !text-[9px] ${
        up
          ? "cyber-chip-ok"
          : unknown
            ? "text-[color:var(--color-cyber-muted)]"
            : "cyber-chip-on"
      }`}
      title={state ?? "unknown"}
    >
      {state ?? "—"}
    </span>
  );
}

export default function NetworkInterfacesPage() {
  const q = useQuery({
    queryKey: ["networks", "interfaces"],
    queryFn: fetchDiag,
    refetchInterval: 10_000,
  });

  const grouped = (q.data?.interfaces ?? []).reduce<
    Record<string, DiagInterface[]>
  >((acc, iface) => {
    const k = classifyKind(iface);
    if (!acc[k]) acc[k] = [];
    acc[k]!.push(iface);
    return acc;
  }, {});
  type Kind = ReturnType<typeof classifyKind>;
  const order: Kind[] = ["bridge", "phy", "wifi", "virtual"];

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Cable className="cyber-glow h-3 w-3" />
          réseau · interfaces
        </div>
        <h1 className="cyber-display cyber-glitch text-4xl" data-text="INTERFACES">
          INTERFACES
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          live snapshot des ifaces L2/L3 du Slate · refresh 10 s
        </p>
      </header>

      <div className="mb-4 flex items-center justify-between">
        <div className="text-[11px] text-[color:var(--color-cyber-muted)]">
          {q.isFetching ? "sync…" : q.data ? `${q.data.interfaces.length} interfaces` : ""}
        </div>
        <button
          type="button"
          onClick={() => q.refetch()}
          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
        >
          <RefreshCw className={`h-3 w-3 ${q.isFetching ? "animate-spin" : ""}`} />
          rafraîchir
        </button>
      </div>

      {q.isError && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(q.error)}
        </p>
      )}

      {q.isLoading && (
        <p className="cyber-label cyber-cursor text-xs">chargement</p>
      )}

      <div className="space-y-6">
        {order
          .filter((k) => grouped[k] && grouped[k]!.length > 0)
          .map((k) => (
            <section key={k} className="cyber-card p-5">
              <header className="mb-3 flex items-center gap-2">
                <KindBadge kind={k} />
                <h2 className="cyber-display cyber-glow text-sm uppercase">
                  {k === "bridge"
                    ? "Bridges"
                    : k === "phy"
                      ? "Physiques / Ethernet"
                      : k === "wifi"
                        ? "Wi-Fi (radios + clients)"
                        : "Virtuelles"}
                </h2>
                <span className="ml-auto text-[10px] text-[color:var(--color-cyber-muted)]">
                  {grouped[k]!.length}
                </span>
              </header>

              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-left text-[9px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
                      <th className="py-2 pr-3">Iface</th>
                      <th className="py-2 pr-3">État</th>
                      <th className="py-2 pr-3">MTU</th>
                      <th className="py-2 pr-3">MAC</th>
                      <th className="py-2 pr-3">Master</th>
                      <th className="py-2 pr-3">Adresses</th>
                      <th className="py-2 pr-3 text-right">
                        <ArrowDown className="inline h-3 w-3" /> RX
                      </th>
                      <th className="py-2 pr-3 text-right">
                        <ArrowUp className="inline h-3 w-3" /> TX
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {grouped[k]!
                      .slice()
                      .sort((a, b) => a.name.localeCompare(b.name))
                      .map((iface) => (
                        <tr
                          key={iface.name}
                          className="border-t border-[color:var(--color-cyber-border)]"
                        >
                          <td className="py-2 pr-3 font-mono text-[color:var(--color-cyber-accent)]">
                            {iface.name}
                          </td>
                          <td className="py-2 pr-3">
                            <OperBadge state={iface.operstate} />
                          </td>
                          <td className="py-2 pr-3 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                            {iface.mtu ?? "—"}
                          </td>
                          <td className="py-2 pr-3 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                            {iface.mac ?? "—"}
                          </td>
                          <td className="py-2 pr-3 font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                            {iface.master ?? "—"}
                          </td>
                          <td className="py-2 pr-3 font-mono text-[10px]">
                            {iface.addresses.length === 0 ? (
                              <span className="text-[color:var(--color-cyber-muted)]">—</span>
                            ) : (
                              <div className="space-y-0.5">
                                {iface.addresses.map((addr, i) => (
                                  <div key={i}>
                                    <span
                                      className={
                                        addr.family === "inet"
                                          ? "text-sky-300"
                                          : "text-purple-300"
                                      }
                                    >
                                      {addr.local}/{addr.prefixlen}
                                    </span>
                                    {addr.scope !== "global" && (
                                      <span className="ml-1 text-[9px] text-[color:var(--color-cyber-muted)]">
                                        ({addr.scope})
                                      </span>
                                    )}
                                  </div>
                                ))}
                              </div>
                            )}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono text-[10px]">
                            {humanBytes(iface.counters?.rx_bytes)}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono text-[10px]">
                            {humanBytes(iface.counters?.tx_bytes)}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
      </div>
    </div>
  );
}
