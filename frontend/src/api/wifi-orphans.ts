// WiFi orphan sections API (Phase 2 cleanup).

import { api } from "./client";

export interface WifiOrphan {
  section: string;
  type: string;
  ssid: string;
  encryption: string;
  device: string;
  network: string;
  disabled: boolean;
  managed: boolean;
  extras: Record<string, string>;
}

export async function listWifiOrphans(): Promise<WifiOrphan[]> {
  const { data } = await api.get<WifiOrphan[]>("/api/wifi/orphans");
  return data;
}

export async function deleteWifiOrphan(section: string): Promise<void> {
  await api.delete(`/api/wifi/orphans/${encodeURIComponent(section)}`);
}

export async function cleanupWifiOrphans(
  sections: string[],
): Promise<Record<string, string>> {
  const { data } = await api.post<Record<string, string>>(
    "/api/wifi/orphans/cleanup-all",
    { sections },
  );
  return data;
}
