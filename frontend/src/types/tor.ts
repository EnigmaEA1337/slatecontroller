/**
 * Tor types. Mirrors backend/app/tor/models.py + the per-network fields
 * surfaced on NetworkPublic.
 *
 * The architecture splits Tor into two layers :
 *   - GLOBAL  (this file's TorSettings / TorBridge / TorStatus) : the
 *             daemon master switch, the bridges, and live status.
 *   - PER-NETWORK (lives on NetworkPublic.tor_route_mode etc.) : which
 *             bridges actually have their traffic redirected through Tor.
 */

export type TorRouteMode = "off" | "transparent" | "socks_only";
export type TorBridgeKind = "obfs4" | "webtunnel" | "snowflake" | "vanilla";

export interface TorSettings {
  daemon_enabled: boolean;
  use_bridges: boolean;
  /** ISO-3166-1 alpha-2 lowercase ("ch", "de"…). Empty = no constraint
   *  (Tor picks the exit freely). When set, the handler emits
   *  `ExitNodes {xx}` + `StrictNodes 1` in torrc. */
  exit_country_code: string;
  updated_at?: string | null;
}

export interface TorSettingsWrite {
  daemon_enabled: boolean;
  use_bridges: boolean;
  exit_country_code: string;
}

export interface TorBridge {
  id: number;
  kind: TorBridgeKind;
  bridge_line: string;
  note: string;
  enabled: boolean;
  created_at: string;
}

export interface TorBridgeWrite {
  kind: TorBridgeKind;
  bridge_line: string;
  note: string;
  enabled: boolean;
}

export interface TorInstallStatus {
  tor: boolean;
  tor_geoipdb: boolean;
  obfs4proxy: boolean;
}

export interface TorRelayHop {
  fingerprint: string;
  nickname: string;
  ip: string | null;
  /** ISO-3166-1 alpha-2 lowercase. null when geoip doesn't cover the IP. */
  country: string | null;
  latitude: number | null;
  longitude: number | null;
  bandwidth_kbps: number | null;
}

export interface TorCircuitInfo {
  circuit_id: string;
  purpose: string;
  build_flags: string[];
  /** Usually 3 hops : entry / middle / exit. */
  hops: TorRelayHop[];
}

export interface TorStatus {
  install: TorInstallStatus;
  daemon_running: boolean;
  control_port_reachable: boolean;
  bootstrap_progress: number | null;
  bootstrap_phase: string | null;
  socks_port: number | null;
  trans_port: number | null;
  dns_port: number | null;
  /** External IP a request right now would come from (= last hop of the
   *  most-recent GENERAL circuit). */
  exit_ip: string | null;
  exit_country: string | null;
  circuits: TorCircuitInfo[];
  uptime_seconds: number | null;
  bytes_read: number | null;
  bytes_written: number | null;
  last_probe_at: string | null;
}
