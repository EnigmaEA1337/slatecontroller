// API client for /api/wifi/ambient — per-(device, band) background WiFi
// scan settings (Q2-A). Drives the AmbientScanManager on the backend
// via PUT — the scheduler is reconfigured immediately, no restart.

import type { WifiBand } from "@/types/wifi";

import { api } from "./client";

export interface AmbientConfig {
  band: WifiBand;
  enabled: boolean;
  interval_s: number;
  retention_days: number;
  last_run_at: string | null;
  last_status: string;
  last_error: string;
  persisted_scans_24h: number;
  persisted_scans_total: number;
}

export interface AmbientConfigUpsert {
  enabled: boolean;
  interval_s: number;
  retention_days: number;
}

export interface AmbientRunNowResult {
  status: string;
  scan_id?: number | null;
  neighbors?: number | null;
}

export interface RecentAmbientScan {
  id: number;
  band: WifiBand;
  started_at: string;
  neighbors_count: number;
  duration_s: number;
}

export async function listAmbientConfigs(): Promise<AmbientConfig[]> {
  const { data } = await api.get<AmbientConfig[]>("/api/wifi/ambient");
  return data;
}

export async function upsertAmbientConfig(
  band: WifiBand,
  body: AmbientConfigUpsert,
): Promise<AmbientConfig> {
  const { data } = await api.put<AmbientConfig>(
    `/api/wifi/ambient/${encodeURIComponent(band)}`,
    body,
  );
  return data;
}

export async function runAmbientNow(
  band: WifiBand,
): Promise<AmbientRunNowResult> {
  const { data } = await api.post<AmbientRunNowResult>(
    `/api/wifi/ambient/${encodeURIComponent(band)}/run-now`,
    null,
    { timeout: 90_000 },
  );
  return data;
}

export async function listRecentAmbientScans(
  limit = 20,
): Promise<RecentAmbientScan[]> {
  const { data } = await api.get<RecentAmbientScan[]>(
    `/api/wifi/ambient/recent?limit=${limit}`,
  );
  return data;
}

// Curated interval choices. 30s is the safe minimum on 5 GHz (DFS dwell).
export const AMBIENT_INTERVALS: ReadonlyArray<{
  value: number;
  label: string;
  hint: string;
}> = [
  { value: 30, label: "30 s", hint: "Très fréquent — réservé 2.4 GHz" },
  { value: 60, label: "1 min", hint: "Standard — équilibre fréquence / charge" },
  { value: 120, label: "2 min", hint: "Léger — économise la radio" },
  { value: 300, label: "5 min", hint: "Surveillance lâche — peu d'historique" },
  { value: 600, label: "10 min", hint: "Échantillonnage rare" },
  { value: 1800, label: "30 min", hint: "Snapshot horaire" },
  { value: 3600, label: "1 h", hint: "Quotidien dilué" },
];
