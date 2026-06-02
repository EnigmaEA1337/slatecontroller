import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Cable,
  Globe,
  Network,
  Shield,
  ShieldCheck,
  ShieldOff,
} from "lucide-react";

import TorCircuitMap from "@/components/TorCircuitMap";
import TorLogsViewer from "@/components/TorLogsViewer";
import TorStatusCard from "@/components/TorStatusCard";
import { getTorStatus } from "@/api/tor";
import { listNetworks } from "@/api/networks";
import { flagFor } from "@/lib/country-coords";
import type { NetworkPublic } from "@/types/network";
import type { TorCircuitInfo, TorRelayHop } from "@/types/tor";

/**
 * Dedicated Tor page (URL : /networks/tor — Réseau group, next to
 * Interfaces / Diagnostic / Réseaux / Radio because Tor is a routing
 * layer, not a protection).
 *
 * Hosts the cross-cutting Tor knobs (status, install, daemon switches,
 * bridges) via `<TorStatusCard />`, plus a per-network summary so the
 * operator can see at a glance which subnets are routed through Tor and
 * jump to the Networks page to change a routing decision.
 *
 * Per-network routing toggles themselves still live on the NetworkForm
 * (Edit a network → "tor · routage per-réseau"), to keep network-shaped
 * decisions next to their network.
 */
export default function ProtectionTor() {
  const networks = useQuery({
    queryKey: ["networks"],
    queryFn: listNetworks,
  });

  // Status is polled by TorStatusCard too — react-query dedupes, this
  // second consumer reuses the same in-flight request / cached payload.
  const status = useQuery({
    queryKey: ["tor", "status"],
    queryFn: getTorStatus,
    refetchInterval: 8_000,
  });

  const grouped = useMemo(() => {
    const all = networks.data ?? [];
    return {
      transparent: all.filter((n) => n.tor_route_mode === "transparent"),
      socks_only: all.filter((n) => n.tor_route_mode === "socks_only"),
      off: all.filter((n) => n.tor_route_mode === "off"),
    };
  }, [networks.data]);

  const totalRouted = grouped.transparent.length + grouped.socks_only.length;
  const circuits = status.data?.circuits ?? [];

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-8">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Shield className="cyber-glow h-3 w-3" />
          réseau · routage anonymisé
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text="TOR"
        >
          TOR
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          onion routing · 3 hops · anti-censure · per-network
        </p>
        <p className="mt-3 max-w-2xl text-xs leading-relaxed text-zinc-400">
          Tor n'est <strong>pas un VPN</strong> — c'est un réseau d'anonymisation
          à 3 sauts (entry → middle → exit) où aucun relais ne sait à la fois
          d'où tu viens et où tu vas. Lent (~1-3 Mbps), latence élevée
          (250-800 ms), exit IPs souvent bloquées par les sites — mais
          anonymat fort + résistant à la censure (bridges obfs4).
        </p>
      </header>

      {/* ── Cross-cutting status / install / daemon / bridges ───────── */}
      <TorStatusCard />

      {/* ── Live logs (tail notices.log) ────────────────────────────── */}
      <TorLogsViewer />

      {/* ── Circuits map (cyber HUD) ─────────────────────────────────── */}
      <section className="mt-6">
        <header className="mb-3 flex items-center justify-between">
          <h2 className="cyber-heading flex items-center gap-2 text-base text-purple-200">
            <Network className="h-4 w-4" />
            Carte des circuits actifs
          </h2>
          <span className="text-[10px] text-zinc-500">
            entry · middle · exit · polled 8s
          </span>
        </header>
        <TorCircuitMap circuits={circuits} />
      </section>

      {/* ── Detailed circuit list ────────────────────────────────────── */}
      {circuits.length > 0 && (
        <section className="mt-6">
          <header className="mb-3 flex items-center gap-2">
            <Cable className="h-4 w-4 text-purple-300" />
            <h2 className="cyber-heading text-base text-purple-200">
              Détail des circuits ({circuits.length})
            </h2>
          </header>
          <ul className="space-y-3">
            {circuits.map((c) => (
              <CircuitRow key={c.circuit_id} circuit={c} />
            ))}
          </ul>
        </section>
      )}

      {/* ── Per-network routing summary ─────────────────────────────── */}
      <section className="mt-6">
        <header className="mb-3 flex items-center justify-between">
          <div>
            <h2 className="cyber-heading text-base text-purple-200">
              Réseaux routés via Tor
            </h2>
            <p className="mt-1 text-[11px] text-zinc-500">
              {totalRouted === 0
                ? "Aucun réseau n'est encore routé via Tor."
                : `${totalRouted} réseau${totalRouted > 1 ? "x" : ""} routé${totalRouted > 1 ? "s" : ""}`}
              {" · "}
              <Link
                to="/networks"
                className="cyber-link inline-flex items-center gap-1 hover:text-purple-200"
              >
                configurer un réseau <ArrowRight className="h-3 w-3" />
              </Link>
            </p>
          </div>
        </header>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <RouteGroup
            label="Transparent"
            hint="tout le TCP du réseau passe par Tor (NAT REDIRECT vers TransPort)"
            icon={<ShieldCheck className="h-4 w-4" />}
            networks={grouped.transparent}
            empty="Aucun réseau en transparent."
            color="emerald"
          />
          <RouteGroup
            label="SOCKS only"
            hint="proxy SOCKS5 sur la gateway:9050, clients opt-in par app"
            icon={<Globe className="h-4 w-4" />}
            networks={grouped.socks_only}
            empty="Aucun réseau en SOCKS only."
            color="cyan"
          />
          <RouteGroup
            label="Off"
            hint="trafic WAN direct, sans Tor"
            icon={<ShieldOff className="h-4 w-4" />}
            networks={grouped.off}
            empty="Tous les réseaux passent par Tor."
            color="zinc"
          />
        </div>
      </section>
    </div>
  );
}

function CircuitRow({ circuit }: { circuit: TorCircuitInfo }) {
  const hops = circuit.hops;
  if (hops.length === 0) return null;
  const exitHop = hops[hops.length - 1];
  return (
    <li className="rounded border border-purple-500/20 bg-zinc-900/40 p-3">
      <div className="mb-2 flex items-center justify-between text-[10px] text-zinc-400">
        <span className="cyber-chip cyber-chip-ghost px-1.5 py-0.5">
          #{circuit.circuit_id}
        </span>
        <span className="flex items-center gap-2">
          <span className="uppercase text-zinc-500">{circuit.purpose || "GENERAL"}</span>
          {circuit.build_flags.length > 0 && (
            <span className="text-zinc-600">
              [{circuit.build_flags.join(", ")}]
            </span>
          )}
        </span>
        {exitHop?.country && (
          <span className="text-emerald-300">
            sortie {flagFor(exitHop.country)} {exitHop.country.toUpperCase()}
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        {hops.map((h, i) => {
          const role =
            i === 0 ? "entry" : i === hops.length - 1 ? "exit" : "middle";
          const accent =
            role === "entry"
              ? "border-cyan-500/40 text-cyan-200"
              : role === "exit"
              ? "border-fuchsia-500/40 text-fuchsia-200"
              : "border-purple-500/40 text-purple-200";
          return <HopCell key={`${h.fingerprint}-${i}`} hop={h} role={role} accent={accent} />;
        })}
      </div>
    </li>
  );
}

function HopCell({
  hop,
  role,
  accent,
}: {
  hop: TorRelayHop;
  role: string;
  accent: string;
}) {
  return (
    <div className={`rounded border ${accent} bg-zinc-950/50 p-2 text-[11px]`}>
      <div className="mb-0.5 flex items-center justify-between">
        <span className="uppercase opacity-70">{role}</span>
        <span className="text-zinc-300">
          {flagFor(hop.country)} {hop.country?.toUpperCase() ?? "??"}
        </span>
      </div>
      <div className="font-semibold text-zinc-100">{hop.nickname || "—"}</div>
      {hop.ip && (
        <div className="font-mono text-[10px] text-zinc-500">{hop.ip}</div>
      )}
      {hop.bandwidth_kbps !== null && (
        <div className="text-[10px] text-zinc-500">
          bw {hop.bandwidth_kbps.toLocaleString()} kbps
        </div>
      )}
    </div>
  );
}

function RouteGroup({
  label,
  hint,
  icon,
  networks,
  empty,
  color,
}: {
  label: string;
  hint: string;
  icon: React.ReactNode;
  networks: NetworkPublic[];
  empty: string;
  color: "emerald" | "cyan" | "zinc";
}) {
  const borderClass =
    color === "emerald"
      ? "border-emerald-500/30 bg-emerald-950/10"
      : color === "cyan"
      ? "border-cyan-500/30 bg-cyan-950/10"
      : "border-zinc-700 bg-zinc-900/40";
  const labelClass =
    color === "emerald"
      ? "text-emerald-300"
      : color === "cyan"
      ? "text-cyan-300"
      : "text-zinc-400";
  return (
    <div className={`rounded border ${borderClass} p-3`}>
      <div className={`mb-1 flex items-center gap-1.5 text-xs font-semibold ${labelClass}`}>
        {icon}
        {label}
        <span className="ml-auto cyber-chip cyber-chip-ghost px-1.5 py-0.5">
          {networks.length}
        </span>
      </div>
      <p className="mb-2 text-[10px] text-zinc-500">{hint}</p>
      {networks.length > 0 ? (
        <ul className="space-y-1 text-xs">
          {networks.map((n) => (
            <li key={n.slug} className="truncate text-zinc-300">
              <span className="cyber-chip cyber-chip-ghost mr-1 px-1.5 py-0.5">
                {n.slug}
              </span>
              {n.display_name}
              {n.tor_route_mode === "transparent" && n.tor_dns_over_tor && (
                <span className="ml-2 text-[10px] text-purple-300">
                  · DNS-via-Tor
                </span>
              )}
              {n.tor_route_mode === "transparent" && n.tor_kill_switch && (
                <span className="ml-2 text-[10px] text-yellow-300">
                  · kill-switch
                </span>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[10px] italic text-zinc-600">{empty}</p>
      )}
    </div>
  );
}
