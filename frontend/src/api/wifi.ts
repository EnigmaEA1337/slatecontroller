import { api } from "./client";
import type { SsidSuggestionsLibrary } from "@/types/wifi-suggestions";
import type { WifiSsidCreate, WifiSsidPublic, WifiSsidWrite } from "@/types/wifi";

export async function listWifiSsids(): Promise<WifiSsidPublic[]> {
  const { data } = await api.get<WifiSsidPublic[]>("/api/wifi");
  return data;
}

export interface WifiReapplyResult {
  ok: boolean;
  output: string;
  needs_reboot: boolean;
}

/** Manual companion to the on-device wifi-drift watchdog. Triggers
 *  ``slate-ctrl apply-only wifi`` against the currently-active profile —
 *  reconciles UCI + ip link without re-running the rest of the
 *  pipeline. Useful when a LCD travel-router toggle drops a managed
 *  VAP and the operator wants to recover faster than the 2-min cron. */
export async function reapplyActiveWifi(): Promise<WifiReapplyResult> {
  const { data } = await api.post<WifiReapplyResult>(
    "/api/wifi/reapply-active",
    undefined,
    { timeout: 90_000 },
  );
  return data;
}

export async function getWifiSsid(slug: string): Promise<WifiSsidPublic> {
  const { data } = await api.get<WifiSsidPublic>(
    `/api/wifi/${encodeURIComponent(slug)}`,
  );
  return data;
}

export async function createWifiSsid(body: WifiSsidCreate): Promise<WifiSsidPublic> {
  const { data } = await api.post<WifiSsidPublic>("/api/wifi", body);
  return data;
}

export async function updateWifiSsid(
  slug: string,
  body: WifiSsidWrite,
): Promise<WifiSsidPublic> {
  const { data } = await api.put<WifiSsidPublic>(
    `/api/wifi/${encodeURIComponent(slug)}`,
    body,
  );
  return data;
}

export async function deleteWifiSsid(slug: string): Promise<void> {
  await api.delete(`/api/wifi/${encodeURIComponent(slug)}`);
}

export async function getSsidSuggestions(): Promise<SsidSuggestionsLibrary> {
  const { data } = await api.get<SsidSuggestionsLibrary>("/api/wifi/suggestions");
  return data;
}

/** Reveal the stored PSK for an SSID. Sensitive — UI must gate behind
 *  an explicit user action and never auto-fetch. */
export async function getSsidPassword(slug: string): Promise<string> {
  const { data } = await api.get<{ slug: string; password: string }>(
    `/api/wifi/${encodeURIComponent(slug)}/password`,
  );
  return data.password;
}

export type WifiSlotKind = "slate_managed" | "glinet_stock" | "mlo_link" | "other";

export interface WifiSlotState {
  section_name: string;
  ifname: string;
  band: string | null;
  mode: string;
  ssid_uci: string;
  ssid_broadcast: string | null;
  enabled: boolean;
  network: string;
  encryption: string;
  is_up: boolean;
  slot_kind: WifiSlotKind;
  marker: boolean;
  notes: string[];
}

/** Live SSH-based probe of the Slate's wireless slots. Slow-ish
 *  (~1-2s) so don't auto-refresh ; UI provides a manual refresh. */
export async function getSlateWifiState(): Promise<WifiSlotState[]> {
  const { data } = await api.get<WifiSlotState[]>("/api/wifi/slate-state", {
    timeout: 20_000,
  });
  return data;
}
