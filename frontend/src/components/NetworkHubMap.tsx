import { useQuery } from "@tanstack/react-query";
import {
  Cable,
  Globe,
  Network as NetIcon,
  Radio,
  Router,
  Shield,
  Wifi,
} from "lucide-react";

import {
  getActiveBridges,
  getActiveSsids,
  getPublicIP,
} from "@/api/observability";
import { ClickableHost } from "@/components/ClickableHost";
import { getTailscaleStatus } from "@/api/tailscale";
import { getTorStatus } from "@/api/tor";
import { listNetworks } from "@/api/networks";
import { flagFor } from "@/lib/country-coords";

/**
 * "At a glance" hub-and-spoke topology for the Dashboard.
 *
 * Pentagon layout : Slate at the centre, 5 satellites around it. Each
 * satellite polls its own React-Query key so it updates independently
 * as the underlying subsystem state changes :
 *
 *   WAN        — `/api/networks/public-ip`   (ipinfo.io via curl on the Slate)
 *   Tor        — `/api/tor/status`            (control port + ports check)
 *   Tailscale  — `/api/tailscale/status`      (CLI parse via SSH)
 *   Réseaux    — list_networks + active-bridges (catalogue vs reality)
 *   Radios     — `/api/networks/active-ssids` (forwarding members)
 *
 * The live dot on each satellite is keyed off whether that subsystem is
 * actually carrying traffic right now — not whether it's *configured*.
 */
export default function NetworkHubMap() {
  const wan = useQuery({
    queryKey: ["dashboard", "public-ip"],
    queryFn: getPublicIP,
    refetchInterval: 60_000,
  });
  const tor = useQuery({
    queryKey: ["tor", "status"],
    queryFn: getTorStatus,
    refetchInterval: 10_000,
  });
  const ts = useQuery({
    queryKey: ["tailscale", "status"],
    queryFn: getTailscaleStatus,
    refetchInterval: 15_000,
  });
  const networks = useQuery({
    queryKey: ["networks"],
    queryFn: listNetworks,
    refetchInterval: 60_000,
  });
  const activeBridges = useQuery({
    queryKey: ["networks", "active-bridges"],
    queryFn: getActiveBridges,
    refetchInterval: 15_000,
  });
  const activeSsids = useQuery({
    queryKey: ["wifi", "active"],
    queryFn: getActiveSsids,
    refetchInterval: 15_000,
  });

  const wanLive = !!wan.data?.ip;
  const torLive = !!tor.data?.daemon_running;
  const tsLive =
    !!ts.data?.daemon_running && ts.data.backend_state === "Running";
  const netsLive = (activeBridges.data?.count ?? 0) > 0;
  const radiosLive = (activeSsids.data?.count ?? 0) > 0;

  // Group active SSIDs by band so the Radios satellite can summarise
  // "2 SSID en 5g · 1 SSID en 6g · MLO" or similar.
  const radioGroups = (activeSsids.data?.ssids ?? []).reduce(
    (acc, s) => {
      const k = s.band || "?";
      acc[k] = (acc[k] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>,
  );
  const uniqueSsids = new Set(
    (activeSsids.data?.ssids ?? []).map((s) => s.ssid),
  );

  return (
    <section className="cyber-card mb-6 p-5">
      <header className="mb-4 flex items-center gap-2">
        <NetIcon className="cyber-glow h-3 w-3 text-[color:var(--color-cyber-accent)]" />
        <h3 className="cyber-label flex-1">
          Topologie · vue d'ensemble
        </h3>
        <span className="text-[10px] uppercase tracking-[0.25em] text-[color:var(--color-cyber-dim)]">
          live · état device
        </span>
      </header>

      <div className="grid grid-cols-3 gap-3 md:gap-4">
        {/* TOP-LEFT : Radios WiFi */}
        <Satellite
          label="Radios WiFi"
          icon={<Radio className="h-3 w-3" />}
          live={radiosLive}
          loading={activeSsids.isLoading}
        >
          <Big>{uniqueSsids.size} SSID</Big>
          <Small>
            {Object.entries(radioGroups)
              .map(([band, n]) => `${n}×${band}`)
              .join(" · ") || "—"}
          </Small>
          <Small dim>
            {Array.from(uniqueSsids).slice(0, 3).join(" · ")}
          </Small>
        </Satellite>

        {/* TOP-CENTER : WAN */}
        <Satellite
          label="WAN"
          icon={<Globe className="h-3 w-3" />}
          live={wanLive}
          loading={wan.isLoading}
        >
          {wan.data?.ip ? (
            <>
              <Big>{wan.data.ip}</Big>
              <Small>
                {flagFor(wan.data.country)} {wan.data.country ?? "—"}
                {wan.data.city && ` · ${wan.data.city}`}
              </Small>
              {wan.data.org && (
                <Small dim className="truncate">
                  {wan.data.org}
                </Small>
              )}
            </>
          ) : (
            <Small dim>indisponible</Small>
          )}
        </Satellite>

        {/* TOP-RIGHT : Tor */}
        <Satellite
          label="Tor"
          icon={<Wifi className="h-3 w-3" />}
          live={torLive}
          loading={tor.isLoading}
        >
          {tor.data?.daemon_running ? (
            <>
              {tor.data.exit_country ? (
                <>
                  <Big>
                    {flagFor(tor.data.exit_country)}{" "}
                    {tor.data.exit_country.toUpperCase()}
                  </Big>
                  <Small className="font-mono text-[10px]">
                    {tor.data.exit_ip ?? "—"}
                  </Small>
                </>
              ) : (
                <Small dim>
                  bootstrap{" "}
                  {tor.data.bootstrap_progress != null
                    ? `${tor.data.bootstrap_progress}%`
                    : "…"}
                </Small>
              )}
              <Small dim>
                {tor.data.circuits.length} circuit
                {tor.data.circuits.length > 1 ? "s" : ""}
              </Small>
            </>
          ) : (
            <Small dim>daemon arrêté</Small>
          )}
        </Satellite>

        {/* MIDDLE-LEFT : Tailscale */}
        <Satellite
          label="Tailscale"
          icon={<Shield className="h-3 w-3" />}
          live={tsLive}
          loading={ts.isLoading}
        >
          {ts.data?.daemon_running ? (
            <>
              <Big className="font-mono">
                {ts.data.tailscale_ips?.[0] ? (
                  <ClickableHost value={ts.data.tailscale_ips[0]} />
                ) : (
                  "—"
                )}
              </Big>
              <Small>
                {ts.data.hostname ? (
                  <ClickableHost value={ts.data.hostname} />
                ) : (
                  "—"
                )}
                {ts.data.tailnet && ` · ${ts.data.tailnet}`}
              </Small>
              <Small dim>
                {ts.data.peers?.length ?? 0} peer
                {(ts.data.peers?.length ?? 0) > 1 ? "s" : ""}
                {" · "}
                {ts.data.backend_state}
              </Small>
            </>
          ) : (
            <Small dim>
              {ts.data?.installed === false ? "non installé" : "arrêté"}
            </Small>
          )}
        </Satellite>

        {/* CENTER : Slate */}
        <div className="flex items-center justify-center">
          <div className="relative">
            <div
              className="cyber-glow rounded-full border-2 p-5"
              style={{
                borderColor: "var(--color-cyber-accent)",
                background: "var(--color-cyber-surface)",
                boxShadow: "0 0 30px rgba(255,58,82,0.4)",
              }}
            >
              <Router
                className="h-10 w-10"
                style={{ color: "var(--color-cyber-accent)" }}
              />
            </div>
            <div className="mt-2 text-center">
              <div className="text-[10px] uppercase tracking-[0.25em] text-[color:var(--color-cyber-dim)]">
                slate
              </div>
              <div className="cyber-glow-soft font-mono text-sm">
                GL-BE10000
              </div>
            </div>
          </div>
        </div>

        {/* MIDDLE-RIGHT : Réseaux (catalogue vs actifs) */}
        <Satellite
          label="Réseaux"
          icon={<Cable className="h-3 w-3" />}
          live={netsLive}
          loading={networks.isLoading || activeBridges.isLoading}
        >
          <Big>
            {activeBridges.data?.count ?? 0}
            <span className="text-xs text-[color:var(--color-cyber-dim)]">
              {" "}
              / {networks.data?.length ?? 0}
            </span>
          </Big>
          <Small dim>actifs / catalogués</Small>
          <Small className="truncate">
            {(activeBridges.data?.bridges ?? [])
              .map((b) => b.replace(/^br-/, ""))
              .join(" · ")}
          </Small>
        </Satellite>
      </div>
    </section>
  );
}

function Satellite({
  label,
  icon,
  live,
  loading,
  children,
}: {
  label: string;
  icon: React.ReactNode;
  live: boolean;
  loading?: boolean;
  children: React.ReactNode;
}) {
  // Reuse the page-wide cyber-card vocabulary so the dashboard reads as
  // one piece. Live = pulsing green ok-color dot ; dim red otherwise.
  return (
    <div className="cyber-card p-3">
      <div className="cyber-label mb-1.5 flex items-center gap-1.5 !text-[9px]">
        <span className="text-[color:var(--color-cyber-accent)]">{icon}</span>
        <span>{label}</span>
        <span
          className={
            "ml-auto inline-block h-1.5 w-1.5 rounded-full " +
            (live
              ? "cyber-pulse bg-[color:var(--color-cyber-ok)]"
              : "bg-[color:var(--color-cyber-border-strong)]")
          }
        />
      </div>
      <div className="text-[color:var(--color-cyber-fg)]">
        {loading ? <Small dim>chargement…</Small> : children}
      </div>
    </div>
  );
}

function Big({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`cyber-glow-soft font-mono text-sm ${className}`}>
      {children}
    </div>
  );
}

function Small({
  children,
  className = "",
  dim = false,
}: {
  children: React.ReactNode;
  className?: string;
  dim?: boolean;
}) {
  const color = dim
    ? "text-[color:var(--color-cyber-dim)]"
    : "text-[color:var(--color-cyber-muted)]";
  return <div className={`text-[10px] ${color} ${className}`}>{children}</div>;
}
