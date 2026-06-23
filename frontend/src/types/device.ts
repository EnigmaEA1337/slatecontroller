export type DeviceStatus = "pending" | "adopted" | "error";
export type DeviceModel = "slate-7-pro" | "mudi-7" | "other";

export interface DevicePublic {
  id: number;
  slug: string;
  label: string;
  model: string;
  host: string;
  /**
   * Ordered list of admin URLs the controller tries with automatic failover
   * (LAN first, then Tailscale, WireGuard tunnel, custom IPv6...). The
   * first one reachable is used. Edited via PATCH /api/devices/{slug}.
   */
  admin_urls: string[];
  rpc_port: number;
  rpc_scheme: "http" | "https";
  ssh_port: number;
  rpc_username: string;
  tls_fingerprint_sha256: string;
  status: DeviceStatus;
  is_default: boolean;
  notes: string;
  /** Serial printed on the tamper-evident sticker covering the chassis
   *  screws. Reference value : if a later physical check shows a
   *  different sticker number, the box was opened. Empty = not tracked. */
  security_label: string;
  last_probe_at: string | null;
  adopted_at: string | null;
  created_at: string;
  has_ssh_keypair: boolean;
  ssh_key_deployed: boolean;
}

export interface DeviceCreate {
  slug: string;
  label?: string;
  model?: DeviceModel;
  host: string;
  rpc_port?: number;
  rpc_scheme?: "http" | "https";
  ssh_port?: number;
  rpc_username: string;
  rpc_password: string;
  notes?: string;
  security_label?: string;
}

export interface AdoptionOptions {
  pin_tls: boolean;
  force_https_webui: boolean;
  ssh_key_only: boolean;
  disable_upnp: boolean;
  // LuCI access is a prerequisite, not an option — the backend
  // unconditionally enables it on every adoption. No checkbox here.
}

export interface AdoptionTaskReport {
  name: string;
  status: "pending" | "running" | "ok" | "skipped" | "failed";
  message: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AdoptionRunReport {
  device_slug: string;
  overall_status: "ok" | "partial" | "failed";
  tasks: AdoptionTaskReport[];
}
