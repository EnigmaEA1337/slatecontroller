// API client for /api/devices/locations — per-device location history.

import { api } from "./client";

export interface DeviceLocationView {
  id: number;
  device_slug: string;
  lat: number;
  lon: number;
  accuracy_m: number | null;
  source: string;
  label: string;
  note: string;
  created_at: string;
}

export interface DeviceLocationsResponse {
  device_slug: string;
  current: DeviceLocationView | null;
  history: DeviceLocationView[];
}

export interface DeviceLocationCreate {
  lat: number;
  lon: number;
  accuracy_m?: number | null;
  source?: string;
  label?: string;
  note?: string;
}

export async function getDeviceLocations(): Promise<DeviceLocationsResponse> {
  const { data } = await api.get<DeviceLocationsResponse>(
    "/api/devices/locations",
  );
  return data;
}

export async function addDeviceLocation(
  body: DeviceLocationCreate,
): Promise<DeviceLocationView> {
  const { data } = await api.post<DeviceLocationView>(
    "/api/devices/locations",
    body,
  );
  return data;
}

export async function deleteDeviceLocation(id: number): Promise<void> {
  await api.delete(`/api/devices/locations/${id}`);
}
