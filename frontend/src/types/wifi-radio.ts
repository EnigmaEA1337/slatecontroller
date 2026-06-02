// Per-band radio (layer-1) types + scanner output. Mirrors
// backend/app/wifi/radio_config.py + backend/app/api/routes/wifi_radio.py.

import type { WifiBand } from "./wifi";

export interface RadioConfigView {
  band: WifiBand;
  channel: number; // 0 = auto / ACS
  htmode: string;
  txpower_percent: number;
  country: string;
  available_htmodes: string[];
}

export interface RadioConfigsResponse {
  device_slug: string;
  bands: Record<WifiBand, RadioConfigView>;
}

export interface RadioConfigPatch {
  channel?: number;
  htmode?: string;
  txpower_percent?: number;
  country?: string;
}

export interface NeighborAPView {
  bssid: string;
  ssid: string;
  hidden: boolean;
  channel: number;
  band: WifiBand;
  rssi_dbm: number;
  security: string;
  ht_mode: string;
  is_wps_enabled: boolean;
  is_ours: boolean;
}

export interface ChannelScoreView {
  band: WifiBand;
  channel: number;
  score: number; // 0-100
  neighbor_count: number;
  is_dfs: boolean;
  is_psc: boolean;
  is_current: boolean;
  reasons: string[];
}

export type ThreatLevel = "info" | "warn" | "alert";

export interface ThreatEventView {
  kind: string;
  level: ThreatLevel;
  bssid: string;
  ssid: string;
  channel: number;
  rssi_dbm: number;
  message: string;
}

export interface ScanResponse {
  band: WifiBand;
  iface: string;
  duration_s: number;
  started_at: number;
  neighbors: NeighborAPView[];
  channel_scores: ChannelScoreView[];
  recommended_channel: number | null;
  current_channel: number | null;
  threats: ThreatEventView[];
}

// Persisted threat events (Air Watch).
export interface ThreatEventDb {
  id: number;
  kind: string;
  level: ThreatLevel;
  bssid: string;
  ssid: string;
  channel: number;
  rssi_dbm: number;
  message: string;
  first_seen_at: string;
  last_seen_at: string;
  dismissed: boolean;
}

export interface AirWatchSummary {
  total: number;
  active: number;
  dismissed: number;
  by_level: Record<string, number>;
  by_kind: Record<string, number>;
  events: ThreatEventDb[];
}
