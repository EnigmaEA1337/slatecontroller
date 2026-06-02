import { api } from "./client";
import type {
  TorBridge,
  TorBridgeWrite,
  TorSettings,
  TorSettingsWrite,
  TorStatus,
} from "@/types/tor";

export async function getTorSettings(): Promise<TorSettings> {
  const { data } = await api.get<TorSettings>("/api/tor/settings");
  return data;
}

export async function updateTorSettings(
  body: TorSettingsWrite,
): Promise<TorSettings> {
  const { data } = await api.put<TorSettings>("/api/tor/settings", body);
  return data;
}

export async function listTorBridges(): Promise<TorBridge[]> {
  const { data } = await api.get<TorBridge[]>("/api/tor/bridges");
  return data;
}

export async function createTorBridge(
  body: TorBridgeWrite,
): Promise<TorBridge> {
  const { data } = await api.post<TorBridge>("/api/tor/bridges", body);
  return data;
}

export async function updateTorBridge(
  id: number,
  body: TorBridgeWrite,
): Promise<TorBridge> {
  const { data } = await api.put<TorBridge>(`/api/tor/bridges/${id}`, body);
  return data;
}

export async function deleteTorBridge(id: number): Promise<void> {
  await api.delete(`/api/tor/bridges/${id}`);
}

export async function getTorLogs(
  limit: number = 200,
): Promise<{ lines: string[] }> {
  const { data } = await api.get<{ lines: string[] }>("/api/tor/logs", {
    params: { limit },
  });
  return data;
}

export async function getTorStatus(): Promise<TorStatus> {
  const { data } = await api.get<TorStatus>("/api/tor/status", {
    // Status is best-effort SSH-side. Default 15s axios timeout is fine
    // because the backend tolerates SSH failures and returns defaults.
  });
  return data;
}

export async function installTor(): Promise<{ ok: string; output: string }> {
  // opkg install can take 30-90 s — bump the timeout.
  const { data } = await api.post<{ ok: string; output: string }>(
    "/api/tor/install",
    {},
    { timeout: 180_000 },
  );
  return data;
}
