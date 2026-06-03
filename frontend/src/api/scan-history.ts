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
}

export interface ScanHistoryDetail extends ScanHistoryRow {
  neighbors: ScanHistoryNeighbor[];
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
