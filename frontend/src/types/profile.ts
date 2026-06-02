// Mirrors backend `app.models.profile.*`.

export type VpnType = "wireguard" | "openvpn" | "none";
export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export interface VPNConfig {
  type: VpnType;
  client: string | null;
  kill_switch: boolean;
}

export type WallpaperKind = "home" | "lock";
export type FitMode = "contain" | "cover" | "stretch";

export interface WallpaperSlotInfo {
  has: boolean;
  fit_mode: FitMode;
  uploaded_at: string | null;
}

export interface ProfileWallpaperMeta {
  profile_name: string;
  kind: WallpaperKind;
  fit_mode: FitMode;
  mime_type: string;
  size_bytes: number;
  uploaded_at: string;
}

export interface TailscaleConnectionOverride {
  accept_routes: boolean | null;
  accept_dns: boolean | null;
  advertise_routes: string[] | null;
  advertise_exit_node: boolean | null;
  exit_node: string | null;
  shields_up: boolean | null;
}

export interface TailscaleHAOverride {
  enabled: boolean | null;
  candidates: string[] | null;
  failsafe_mode: "fail_open" | "keep" | null;
}

export interface TailscaleConfig {
  enabled: boolean;
  admin_only: boolean;
  connection: TailscaleConnectionOverride | null;
  ha: TailscaleHAOverride | null;
}

export interface ProfileSSIDRef {
  slug: string;
  enabled: boolean;
  // L2→L3 binding : which network (bridge/subnet) this SSID routes to
  // when this profile is active. Same SSID can map to different networks
  // across profiles. Defaults to "lan".
  network_slug: string;
}

export interface FirewallConfig {
  lockdown: boolean;
  geoip_whitelist: string[];
  block_telemetry: boolean;
  block_all_outbound: boolean;
}

export interface LoggingConfig {
  level: LogLevel;
  forward_to_siem: boolean;
}

export interface Profile {
  name: string;
  description: string;
  icon: string | null;
  color: string | null;
  vpn: VPNConfig;
  tailscale: TailscaleConfig;
  // adguard: removed — filtering / blocklists are driven per-network
  //   by the DNS protection manager (Networks page).
  // dns: removed — DNS protection is per-network now (Networks page).
  // tor: removed — daemon switch + bridges + exit_country are global
  //   (TorSettings, Réseau → Tor) ; routing is per-network
  //   (NetworkPublic.tor_route_mode).
  ssids: ProfileSSIDRef[];
  firewall: FirewallConfig;
  logging: LoggingConfig;
}

export type ProfileSource = "template" | "user";

export interface ScoreItem {
  name: string;
  points: number;
  max_points: number;
  note: string;
}

export interface ProfileScores {
  anonymization: number; // 0-100
  security: number; // 0-100
  breakdown_anonymization: ScoreItem[];
  breakdown_security: ScoreItem[];
}

export interface ProfileEnvelope {
  profile: Profile;
  source: ProfileSource;
  is_active: boolean;
  scores: ProfileScores;
  created_at: string;
  updated_at: string;
  wallpapers: Record<WallpaperKind, WallpaperSlotInfo>;
}

export interface ActiveProfileResponse {
  active_name: string | null;
  profile: Profile | null;
}
