// API client for /api/wifi/scan-history.

import { api } from "./client";
import type { WifiBand } from "@/types/wifi";

export interface ScanHistoryRow {
  id: number;
  device_slug: string;
  band: WifiBand;
  iface: string;
  started_at: string;
  duration_s: number;
  lat: number | null;
  lon: number | null;
  accuracy_m: number | null;
  source: string;
  neighbors_count: number;
  threats_count: number;
  recommended_channel: number | null;
  current_channel: number | null;
  note: string;
}

export interface ScanHistoryNeighbor {
  bssid: string;
  ssid: string;
  hidden: boolean;
  channel: number;
  band: WifiBand;
  rssi_dbm: number;
  security: string;
  ht_mode: string;
  is_wps_enabled: boolean;
  ap_root: string;
  vendor: string;
  vendor_slug: string;
  is_randomized: boolean;
  review_status_own?: string | null;
  review_status_effective?: string | null;
  review_label_own?: string;
  seen_count?: number;
  rssi_max?: number;
  rssi_min?: number;
  first_seen_offset_s?: number;
  last_seen_offset_s?: number;
}

export interface ScanHistoryChannelScore {
  band: WifiBand;
  channel: number;
  score: number;
  neighbor_count: number;
  is_dfs: boolean;
  is_psc: boolean;
  is_current: boolean;
  reasons: string[];
}

export interface ScanHistoryPhysicalAP {
  ap_root: string;
  channel: number;
  channels: number[];
  bands: WifiBand[];
  rssi_dbm: number;
  vendor: string;
  vendor_slug: string;
  is_all_randomized: boolean;
  has_wps: boolean;
  ssids: string[];
  hidden_count: number;
  member_count: number;
  bssids: string[];
  review_status: string | null;
  review_label: string;
}

export interface ScanHistoryDetail extends ScanHistoryRow {
  neighbors: ScanHistoryNeighbor[];
  channel_scores: ScanHistoryChannelScore[];
  physical_aps: ScanHistoryPhysicalAP[];
}

export async function listScanHistory(opts?: {
  band?: WifiBand;
  limit?: number;
}): Promise<ScanHistoryRow[]> {
  const params = new URLSearchParams();
  if (opts?.band) params.set("band", opts.band);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const { data } = await api.get<ScanHistoryRow[]>(
    `/api/wifi/scan-history${qs ? `?${qs}` : ""}`,
  );
  return data;
}

export async function getScanHistoryDetail(
  id: number,
): Promise<ScanHistoryDetail> {
  const { data } = await api.get<ScanHistoryDetail>(
    `/api/wifi/scan-history/${id}`,
  );
  return data;
}

export async function deleteScanHistory(id: number): Promise<void> {
  await api.delete(`/api/wifi/scan-history/${id}`);
}
