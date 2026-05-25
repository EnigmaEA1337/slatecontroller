export type WifiBand = "2GHz" | "5GHz" | "6GHz" | "MLO";
export type WifiSecurity =
  | "WPA3-SAE"
  | "WPA3-PSK"
  | "WPA2-PSK"
  | "WPA2-WPA3-Mixed"
  | "open";

export interface WifiSsidPublic {
  slug: string;
  ssid_name: string;
  band: WifiBand;
  security: WifiSecurity;
  network_slug: string;
  client_isolation: boolean;
  notes: string;
  has_password: boolean;
  created_at: string;
  updated_at: string;
}

export interface WifiSsidWrite {
  ssid_name: string;
  band: WifiBand;
  security: WifiSecurity;
  password: string | null; // null = leave alone (update); "" = clear; "..." = set
  network_slug: string;
  client_isolation: boolean;
  notes: string;
}

export interface WifiSsidCreate extends WifiSsidWrite {
  slug: string;
}
