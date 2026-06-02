import { api } from "./client";

export interface PublicIPInfo {
  ip: string | null;
  country: string | null;
  city: string | null;
  region: string | null;
  org: string | null;
  latitude: number | null;
  longitude: number | null;
}

export interface SpeedtestResult {
  ping_ms: number | null;
  jitter_ms: number | null;
  packet_loss_pct: number | null;
  download_mbps: number | null;
  upload_mbps: number | null;
  server: string;
  bytes_downloaded: number | null;
  bytes_uploaded: number | null;
  error: string | null;
}

export interface ActiveSsid {
  ifname: string;
  ssid: string;
  band: string;       // "2g" / "5g" / "6g"
  bridge: string;     // "br-nexus" etc.
}

export async function getPublicIP(): Promise<PublicIPInfo> {
  const { data } = await api.get<PublicIPInfo>("/api/networks/public-ip");
  return data;
}

export async function getActiveBridges(): Promise<{ bridges: string[]; count: number }> {
  const { data } = await api.get<{ bridges: string[]; count: number }>(
    "/api/networks/active-bridges",
  );
  return data;
}

export async function getActiveSsids(): Promise<{ ssids: ActiveSsid[]; count: number }> {
  const { data } = await api.get<{ ssids: ActiveSsid[]; count: number }>(
    "/api/networks/active-ssids",
  );
  return data;
}

export async function runSpeedtest(): Promise<SpeedtestResult> {
  // Speedtest takes 20-30s on a typical link; bump the axios timeout
  // well above the default 15s so the spinner doesn't lie.
  const { data } = await api.post<SpeedtestResult>(
    "/api/networks/speedtest",
    {},
    { timeout: 60_000 },
  );
  return data;
}
