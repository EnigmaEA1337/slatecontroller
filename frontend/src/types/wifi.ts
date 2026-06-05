// Compact tokens : "2" = 2.4 GHz, "5", "6". MLO (Wi-Fi 7 Multi-Link)
// is expressed via the separate `mlo` boolean, not a band value.
export type WifiBand = "2" | "5" | "6";
export type WifiSecurity =
  | "WPA3-SAE"
  | "WPA3-PSK"
  | "WPA2-PSK"
  | "WPA2-WPA3-Mixed"
  | "open";

// Protected Management Frames (802.11w) policy.
export type WifiPMF = "disabled" | "optional" | "required";

// MTK-specific knobs exposed under the "Avancé" section of the SSID
// edit form. Each maps 1:1 to a UCI option the MT7990 driver honours.
// The defaults preserve the legacy behaviour for SSIDs created before
// this section existed.
export interface WifiSsidAdvanced {
  pmf: WifiPMF;
  ft_802_11r: boolean;
  rrm_802_11k: boolean;
  btm_802_11v: boolean;
  dtim_period: number; // 1-10
  wmm: boolean;
  proxy_arp: boolean;
  wds: boolean;
}

export const DEFAULT_ADVANCED: WifiSsidAdvanced = {
  pmf: "optional",
  ft_802_11r: false,
  rrm_802_11k: false,
  btm_802_11v: false,
  dtim_period: 2,
  wmm: true,
  proxy_arp: false,
  wds: false,
};

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
  advanced: WifiSsidAdvanced;
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
  advanced: WifiSsidAdvanced;
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
