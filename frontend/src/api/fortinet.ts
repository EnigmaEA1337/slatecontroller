import { api } from "./client";
import type {
  FortinetConfigCreate,
  FortinetConfigPublic,
  FortinetConfigUpdate,
  FortinetPreflight,
  FortinetReconcileReport,
  FortinetStatusReport,
} from "@/types/fortinet";

export async function listFortinetConfigs(): Promise<FortinetConfigPublic[]> {
  const { data } = await api.get<FortinetConfigPublic[]>("/api/vpn/fortinet");
  return data;
}

export async function getFortinetConfig(
  slug: string,
): Promise<FortinetConfigPublic> {
  const { data } = await api.get<FortinetConfigPublic>(
    `/api/vpn/fortinet/${encodeURIComponent(slug)}`,
  );
  return data;
}

export async function createFortinetConfig(
  body: FortinetConfigCreate,
): Promise<FortinetConfigPublic> {
  const { data } = await api.post<FortinetConfigPublic>(
    "/api/vpn/fortinet",
    body,
  );
  return data;
}

export async function patchFortinetConfig(
  slug: string,
  body: FortinetConfigUpdate,
): Promise<FortinetConfigPublic> {
  const { data } = await api.patch<FortinetConfigPublic>(
    `/api/vpn/fortinet/${encodeURIComponent(slug)}`,
    body,
  );
  return data;
}

export async function deleteFortinetConfig(slug: string): Promise<void> {
  await api.delete(`/api/vpn/fortinet/${encodeURIComponent(slug)}`);
}

export async function getFortinetStatus(): Promise<FortinetStatusReport> {
  const { data } = await api.get<FortinetStatusReport>(
    "/api/vpn/fortinet/status",
    { timeout: 15_000 },
  );
  return data;
}

export async function getFortinetPreflight(): Promise<FortinetPreflight> {
  const { data } = await api.get<FortinetPreflight>(
    "/api/vpn/fortinet/preflight",
    { timeout: 15_000 },
  );
  return data;
}

export async function connectFortinet(
  slug: string,
  otp: string,
  overrides?: { username?: string; password?: string },
): Promise<FortinetStatusReport> {
  // openfortivpn auth + ppp negotiation can take 8-25 s ; 2FA push wait
  // can stretch up to 60 s. Give the request room.
  const body: Record<string, string> = { otp };
  if (overrides?.username) body.username = overrides.username;
  if (overrides?.password) body.password = overrides.password;
  const { data } = await api.post<FortinetStatusReport>(
    `/api/vpn/fortinet/${encodeURIComponent(slug)}/connect`,
    body,
    { timeout: 90_000 },
  );
  return data;
}

export async function getFortinetLogs(
  lines: number = 200,
): Promise<{ lines: string[]; truncated: boolean }> {
  const { data } = await api.get<{ lines: string[]; truncated: boolean }>(
    "/api/vpn/fortinet/logs",
    { params: { lines }, timeout: 15_000 },
  );
  return data;
}

export async function disconnectFortinet(): Promise<FortinetStatusReport> {
  const { data } = await api.post<FortinetStatusReport>(
    "/api/vpn/fortinet/disconnect",
    undefined,
    { timeout: 30_000 },
  );
  return data;
}

export async function reconcileFortinetRouting(): Promise<FortinetReconcileReport> {
  const { data } = await api.post<FortinetReconcileReport>(
    "/api/vpn/fortinet/network-routing/reconcile",
    undefined,
    { timeout: 30_000 },
  );
  return data;
}

// ── builder + sideload ────────────────────────────────────────────────

export interface FortinetBuildArtifact {
  available: boolean;
  path: string;
  size_bytes: number;
  sha256: string;
  version: string;
  git_ref: string;
  built_at_seconds: number;
}

export async function getFortinetArtifact(): Promise<FortinetBuildArtifact> {
  const { data } = await api.get<FortinetBuildArtifact>(
    "/api/vpn/fortinet/build/artifact",
  );
  return data;
}

export interface FortinetBuildResult {
  ok: boolean;
  rc: number;
  logs: string;
  artifact: FortinetBuildArtifact;
}

export async function buildFortinetBinary(
  ref: string = "v1.21.0",
): Promise<FortinetBuildResult> {
  // openssl static build + openfortivpn = up to ~12 min on slow CPUs.
  const { data } = await api.post<FortinetBuildResult>(
    "/api/vpn/fortinet/build",
    { openfortivpn_ref: ref },
    { timeout: 25 * 60_000 },
  );
  return data;
}

export interface FortinetSideloadResult {
  ok: boolean;
  remote_path: string;
  size_bytes: number;
  version: string;
  sha256: string;
}

export async function sideloadFortinetBinary(): Promise<FortinetSideloadResult> {
  const { data } = await api.post<FortinetSideloadResult>(
    "/api/vpn/fortinet/build/sideload",
    undefined,
    { timeout: 60_000 },
  );
  return data;
}
