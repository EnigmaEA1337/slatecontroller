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

/** Trigger a live scan. Slow-ish (10-25s) — UI must show progress. */
export async function scanRadio(band: WifiBand): Promise<ScanResponse> {
  const { data } = await api.post<ScanResponse>(
    `/api/wifi/radios/${encodeURIComponent(band)}/scan`,
    null,
    { timeout: 60_000 },
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
