import { api } from "./client";
import type { SsidSuggestionsLibrary } from "@/types/wifi-suggestions";
import type { WifiSsidCreate, WifiSsidPublic, WifiSsidWrite } from "@/types/wifi";

export async function listWifiSsids(): Promise<WifiSsidPublic[]> {
  const { data } = await api.get<WifiSsidPublic[]>("/api/wifi");
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
