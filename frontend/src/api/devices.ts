import { api } from "./client";
import type {
  AdoptionOptions,
  AdoptionRunReport,
  DeviceCreate,
  DevicePublic,
} from "@/types/device";

export async function listDevices(): Promise<DevicePublic[]> {
  const { data } = await api.get<DevicePublic[]>("/api/devices");
  return data;
}

export async function getDevice(slug: string): Promise<DevicePublic> {
  const { data } = await api.get<DevicePublic>(`/api/devices/${slug}`);
  return data;
}

export async function createDevice(body: DeviceCreate): Promise<DevicePublic> {
  const { data } = await api.post<DevicePublic>("/api/devices", body);
  return data;
}

export async function deleteDevice(slug: string): Promise<void> {
  await api.delete(`/api/devices/${slug}`);
}

export async function probeDevice(slug: string): Promise<DevicePublic> {
  const { data } = await api.post<DevicePublic>(`/api/devices/${slug}/probe`);
  return data;
}

export async function setDefaultDevice(slug: string): Promise<DevicePublic> {
  const { data } = await api.post<DevicePublic>(`/api/devices/${slug}/default`);
  return data;
}

export async function adoptDevice(
  slug: string,
  body: AdoptionOptions,
): Promise<AdoptionRunReport> {
  // Adoption now runs 7 tasks including a full agent deploy (push of
  // slate-ctrl + 10 handlers + secrets + scripts + cron). On a slow
  // uplink that easily takes 60s — way above axios' default 15s. Give
  // it 180s so the operator never sees a misleading frontend timeout
  // while the backend is still doing work.
  const { data } = await api.post<AdoptionRunReport>(
    `/api/devices/${slug}/adopt`,
    body,
    { timeout: 180_000 },
  );
  return data;
}

/** Reset the controller's local adoption state for this device. No SSH /
 * no Slate modification — just clears `status` + `adopted_at` so the
 * adoption flow re-opens in the UI. Re-using this device after a forget
 * preserves credentials, TLS pin, admin URLs, SSH keypair. */
export async function forgetDevice(slug: string): Promise<DevicePublic> {
  const { data } = await api.post<DevicePublic>(`/api/devices/${slug}/forget`);
  return data;
}

export interface FactoryResetReport {
  device_slug: string;
  started: boolean;
  note: string;
}

/** DESTRUCTIVE — wipes the Slate (firstboot + reboot) and resets the
 * device locally to pending. Requires `confirmSlug` to match the device
 * slug exactly (typed confirmation). */
export async function factoryResetDevice(
  slug: string,
  confirmSlug: string,
): Promise<FactoryResetReport> {
  const { data } = await api.post<FactoryResetReport>(
    `/api/devices/${slug}/factory-reset`,
    { confirm_slug: confirmSlug },
  );
  return data;
}

// Update mutable fields of a device. Used today to edit `admin_urls` (the
// ordered list of LAN/Tailscale/WireGuard URLs the controller tries for
// failover), but supports any PATCH-able field.
export interface DevicePatch {
  label?: string;
  host?: string;
  admin_urls?: string[];
  rpc_port?: number;
  ssh_port?: number;
  rpc_username?: string;
  rpc_password?: string;
  notes?: string;
}

export async function patchDevice(
  slug: string,
  body: DevicePatch,
): Promise<DevicePublic> {
  const { data } = await api.patch<DevicePublic>(`/api/devices/${slug}`, body);
  return data;
}

// Live connectivity status for the active device — used by the badge in
// the layout header and by the device edit modal.
export interface ConnectivityCandidate {
  url: string;
  host: string;
  reachable: boolean;
  latency_ms: number | null;
}

export interface ConnectivityStatus {
  active_url: string;
  candidates: ConnectivityCandidate[];
}

export async function getSlateConnectivity(
  forceRefresh = false,
): Promise<ConnectivityStatus> {
  const params = forceRefresh ? "?force_refresh=1" : "";
  const { data } = await api.get<ConnectivityStatus>(
    `/api/slate/connectivity${params}`,
  );
  return data;
}
