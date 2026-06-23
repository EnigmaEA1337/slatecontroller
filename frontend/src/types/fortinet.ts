export type FortinetState =
  | "unknown"
  | "connecting"
  | "up"
  | "disconnecting"
  | "down"
  | "failed";

export interface FortinetConfigPublic {
  slug: string;
  display_name: string;
  gateway_host: string;
  gateway_port: number;
  username: string;
  trusted_cert_sha256: string;
  has_ca_cert: boolean;
  has_password: boolean;
  notes: string;
  last_status: FortinetState;
  last_connected_at: string | null;
  last_disconnected_at: string | null;
  last_error: string;
  created_at: string;
  updated_at: string;
}

export interface FortinetConfigCreate {
  slug: string;
  display_name?: string;
  gateway_host: string;
  gateway_port?: number;
  username: string;
  password: string;
  trusted_cert_sha256?: string;
  ca_cert_pem?: string;
  notes?: string;
}

export interface FortinetConfigUpdate {
  display_name?: string;
  gateway_host?: string;
  gateway_port?: number;
  username?: string;
  password?: string;
  trusted_cert_sha256?: string;
  ca_cert_pem?: string;
  notes?: string;
}

export interface FortinetStatusReport {
  slug: string | null;
  state: FortinetState;
  ppp_iface: string | null;
  tunnel_ip: string | null;
  gateway_ip: string | null;
  rx_bytes: number;
  tx_bytes: number;
  uptime_seconds: number;
  last_error: string;
}

export interface FortinetPreflight {
  ok: boolean;
  binary: string;
  version: string;
  ppp_kmod: boolean;
  error: string;
}

export interface FortinetReconcileReport {
  tunnel_up: boolean;
  ppp_iface: string | null;
  wan_iface: string | null;
  networks: string[];
  applied_lines: number;
}

/** Body of POST /{slug}/connect. The mobile login flow types user+pass
 *  fresh every call. Desktop flow stores them in the config and leaves
 *  both `null` to fall back to the stored secret. */
export interface FortinetConnectRequest {
  otp: string;
  username?: string | null;
  password?: string | null;
}

export interface FortinetLogsResponse {
  lines: string[];
  truncated: boolean;
}
