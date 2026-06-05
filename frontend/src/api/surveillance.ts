// API client for /api/wifi/surveillance — named time-bounded WiFi
// scanning sessions with per-BSSID classification (Q2-C).

import { api } from "./client";

export type SessionStatus = "active" | "completed" | "cancelled" | "failed";

export interface SurveillanceSession {
  id: number;
  name: string;
  status: SessionStatus;
  started_at: string;
  ended_at: string | null;
  target_duration_s: number;
  interval_s: number;
  bands: string;
  location_lat: number | null;
  location_lon: number | null;
  location_label: string;
  note: string;
  total_passes: number;
  unique_bssids: number;
}

export interface SurveillanceCreate {
  name: string;
  bands: string;
  target_duration_s: number;
  interval_s: number;
  location_lat?: number | null;
  location_lon?: number | null;
  location_label?: string;
  note?: string;
}

export type Classification = "stable" | "edge" | "drifting" | "transient";

export interface TimelineBucket {
  index: number;
  start_offset_s: number;
  end_offset_s: number;
}

export interface TimelineRowBucket {
  idx: number;
  rssi_dbm: number | null;
}

export interface TimelineRow {
  bssid: string;
  ssid: string;
  vendor: string;
  vendor_slug: string;
  channel: number;
  band: string;
  ap_root: string;
  is_randomized: boolean;
  rssi_max: number;
  rssi_min: number;
  rssi_drift: number;
  passes_seen: number;
  presence_ratio: number;
  classification: Classification;
  buckets: TimelineRowBucket[];
}

export interface TimelinePayload {
  session: {
    id: number;
    name: string;
    status: SessionStatus;
    started_at: string;
    ended_at: string | null;
    target_duration_s: number;
    interval_s: number;
    bands: string;
    location_lat: number | null;
    location_lon: number | null;
    location_label: string;
    note: string;
    total_passes: number;
    unique_bssids: number;
    window_s: number;
  };
  buckets: TimelineBucket[];
  rows: TimelineRow[];
}

export async function listSurveillanceSessions(): Promise<
  SurveillanceSession[]
> {
  const { data } = await api.get<SurveillanceSession[]>(
    "/api/wifi/surveillance",
  );
  return data;
}

export async function createSurveillanceSession(
  body: SurveillanceCreate,
): Promise<SurveillanceSession> {
  const { data } = await api.post<SurveillanceSession>(
    "/api/wifi/surveillance",
    body,
  );
  return data;
}

export async function getSurveillanceSession(
  id: number,
): Promise<SurveillanceSession> {
  const { data } = await api.get<SurveillanceSession>(
    `/api/wifi/surveillance/${id}`,
  );
  return data;
}

export async function cancelSurveillanceSession(
  id: number,
): Promise<SurveillanceSession> {
  const { data } = await api.post<SurveillanceSession>(
    `/api/wifi/surveillance/${id}/cancel`,
  );
  return data;
}

export async function deleteSurveillanceSession(id: number): Promise<void> {
  await api.delete(`/api/wifi/surveillance/${id}`);
}

export async function getSurveillanceTimeline(
  id: number,
  numBuckets = 80,
): Promise<TimelinePayload> {
  const { data } = await api.get<TimelinePayload>(
    `/api/wifi/surveillance/${id}/timeline?num_buckets=${numBuckets}`,
  );
  return data;
}

// UX presets — keeps both creation form and detail view consistent.
export const CLASSIFICATION_META: Record<
  Classification,
  { label: string; color: string; hint: string; icon: string }
> = {
  stable: {
    label: "stable",
    color: "#34d399",
    hint: "Présence ≥ 80% et drift RSSI < 5 dB — infra fixe",
    icon: "■",
  },
  edge: {
    label: "edge",
    color: "#fbbf24",
    hint: "Présence ≥ 50% mais drift > 5 dB — fixe en limite",
    icon: "◆",
  },
  drifting: {
    label: "drifting",
    color: "#60a5fa",
    hint: "20% ≤ présence < 50% — apparaît périodiquement",
    icon: "◇",
  },
  transient: {
    label: "transient",
    color: "#94a3b8",
    hint: "Présence < 20% — device mobile qui passait",
    icon: "·",
  },
};

export const SURVEILLANCE_PRESETS: ReadonlyArray<{
  label: string;
  duration_s: number;
  hint: string;
}> = [
  { label: "5 min", duration_s: 5 * 60, hint: "Test rapide" },
  { label: "30 min", duration_s: 30 * 60, hint: "Réunion / passage" },
  { label: "1 h", duration_s: 60 * 60, hint: "Vue d'ensemble courte" },
  { label: "2 h", duration_s: 2 * 60 * 60, hint: "Café, salle, hall" },
  { label: "4 h", duration_s: 4 * 60 * 60, hint: "Journée travail" },
  { label: "8 h", duration_s: 8 * 60 * 60, hint: "Surveillance étendue" },
  { label: "24 h", duration_s: 24 * 60 * 60, hint: "Cycle complet" },
];
