export type VpnProvider = "proton" | "other";

export interface VPNConfigPublic {
  name: string;
  provider: VpnProvider;
  interface_address: string;
  dns_servers: string[];
  peer_public_key: string;
  peer_endpoint: string;
  peer_allowed_ips: string[];
  created_at: string;
}

export interface VPNConfigUploadResponse {
  name: string;
  provider: VpnProvider;
  peer_endpoint: string;
}
