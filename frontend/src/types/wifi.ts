// Compact tokens : "2" = 2.4 GHz, "5", "6". MLO (Wi-Fi 7 Multi-Link)
// is expressed via the separate `mlo` boolean, not a band value.
export type WifiBand = "2" | "5" | "6";
export type WifiSecurity =
  | "WPA3-SAE"
  | "WPA3-PSK"
  | "WPA2-PSK"
  | "WPA2-WPA3-Mixed"
  | "open";

// NB: no network_slug. An SSID is a pure L2 access definition; the
// network (bridge/subnet) binding is a per-profile decision and lives
// on ProfileSSIDRef.network_slug instead.
export interface WifiSsidPublic {
  slug: string;
  ssid_name: string;
  bands: WifiBand[];
  mlo: boolean;
  security: WifiSecurity;
  client_isolation: boolean;
  hidden: boolean;
  notes: string;
  has_password: boolean;
  created_at: string;
  updated_at: string;
}

export interface WifiSsidWrite {
  ssid_name: string;
  bands: WifiBand[];
  mlo: boolean;
  security: WifiSecurity;
  password: string | null; // null = leave alone (update); "" = clear; "..." = set
  client_isolation: boolean;
  hidden: boolean;
  notes: string;
}

export interface WifiSsidCreate extends WifiSsidWrite {
  slug: string;
}

// Human-readable label for a band token (used in chips, plan summaries).
export function labelForBand(b: WifiBand): string {
  switch (b) {
    case "2": return "2.4 GHz";
    case "5": return "5 GHz";
    case "6": return "6 GHz";
  }
}
