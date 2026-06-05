// API client for /api/wifi/radios (layer-1 config + scanner) and
// /api/security/air-watch (threat detection timeline).

import { api } from "./client";
import type {
  AirWatchSummary,
  RadioConfigPatch,
  RadioConfigsResponse,
  RadioConfigView,
  ScanResponse,
  ThreatEventDb,
} from "@/types/wifi-radio";
import type { WifiBand } from "@/types/wifi";

export async function getRadioConfigs(): Promise<RadioConfigsResponse> {
  const { data } = await api.get<RadioConfigsResponse>("/api/wifi/radios");
  return data;
}

export async function updateRadioConfig(
  band: WifiBand,
  patch: RadioConfigPatch,
): Promise<RadioConfigView> {
  const { data } = await api.put<RadioConfigView>(
    `/api/wifi/radios/${encodeURIComponent(band)}`,
    patch,
  );
  return data;
}

/** Trigger a live scan.
 *
 * - durationS = 0 (default) : single pass, slow-ish (10-25s).
 * - durationS > 0 : multi-pass extended scan ; loops iw scans until the
 *   wall-clock budget is spent, merges by BSSID, exposes per-BSSID stats
 *   (seen_count, rssi_max/min, first/last_seen_offset_s). Backend caps
 *   at 1200s (20 min) for safety.
 */
export async function scanRadio(
  band: WifiBand,
  opts?: { durationS?: number },
): Promise<ScanResponse> {
  const durationS = opts?.durationS ?? 0;
  // HTTP timeout : duration_s budget + 30s slack for the last pass +
  // network overhead. Default (single pass) keeps the original 60s.
  const httpTimeout =
    durationS > 0 ? (durationS + 30) * 1000 : 60_000;
  const params = new URLSearchParams();
  if (durationS > 0) params.set("duration_s", String(durationS));
  const qs = params.toString();
  const { data } = await api.post<ScanResponse>(
    `/api/wifi/radios/${encodeURIComponent(band)}/scan${qs ? `?${qs}` : ""}`,
    null,
    { timeout: httpTimeout },
  );
  return data;
}

export async function getAirWatchSummary(): Promise<AirWatchSummary> {
  const { data } = await api.get<AirWatchSummary>("/api/security/air-watch");
  return data;
}

export async function dismissThreat(
  id: number,
): Promise<ThreatEventDb> {
  const { data } = await api.post<ThreatEventDb>(
    `/api/security/air-watch/${id}/dismiss`,
  );
  return data;
}

export async function restoreThreat(
  id: number,
): Promise<ThreatEventDb> {
  const { data } = await api.post<ThreatEventDb>(
    `/api/security/air-watch/${id}/restore`,
  );
  return data;
}
