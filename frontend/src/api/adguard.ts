import { api } from "./client";
import type {
  AdGuardFilter,
  AdGuardStats,
  AdGuardStatus,
} from "@/types/adguard";

export async function getAdGuardStatus(): Promise<AdGuardStatus> {
  const { data } = await api.get<AdGuardStatus>("/api/adguard/status");
  return data;
}

export async function toggleAdGuard(enabled: boolean): Promise<AdGuardStatus> {
  const { data } = await api.post<AdGuardStatus>("/api/adguard/toggle", {
    enabled,
  });
  return data;
}

export async function setAdGuardProtection(
  enabled: boolean,
): Promise<AdGuardStatus> {
  const { data } = await api.post<AdGuardStatus>("/api/adguard/protection", {
    enabled,
  });
  return data;
}

export async function getAdGuardStats(): Promise<AdGuardStats> {
  const { data } = await api.get<AdGuardStats>("/api/adguard/stats");
  return data;
}

export async function listAdGuardFilters(): Promise<AdGuardFilter[]> {
  const { data } = await api.get<AdGuardFilter[]>("/api/adguard/filters");
  return data;
}

export async function addAdGuardFilter(
  body: { url: string; name: string },
): Promise<AdGuardFilter[]> {
  const { data } = await api.post<AdGuardFilter[]>("/api/adguard/filters", body);
  return data;
}

export async function toggleAdGuardFilter(
  body: { url: string; enabled: boolean },
): Promise<AdGuardFilter[]> {
  const { data } = await api.patch<AdGuardFilter[]>(
    "/api/adguard/filters",
    body,
  );
  return data;
}

export async function removeAdGuardFilter(url: string): Promise<AdGuardFilter[]> {
  const { data } = await api.delete<AdGuardFilter[]>("/api/adguard/filters", {
    params: { url },
  });
  return data;
}

export async function refreshAdGuardFilters(): Promise<void> {
  await api.post("/api/adguard/filters/refresh");
}

export interface FeedEntry {
  slug: string;
  name: string;
  description: string;
  url: string;
  category: string;
  maintainer: string;
  intensity: "light" | "balanced" | "pro" | "hard";
  recommended: boolean;
  active: boolean;
}

export async function getAdGuardFeedCatalog(): Promise<FeedEntry[]> {
  const { data } = await api.get<FeedEntry[]>("/api/adguard/feeds/catalog");
  return data;
}

export async function applyAdGuardFeeds(slugs: string[]): Promise<unknown> {
  const { data } = await api.post("/api/adguard/feeds/apply", { slugs });
  return data;
}

export interface DnssecStatus {
  enabled: boolean;
  upstream_dns: string[];
  fallback_dns: string[];
}

export async function getAdGuardDnssec(): Promise<DnssecStatus> {
  const { data } = await api.get<DnssecStatus>("/api/adguard/dnssec");
  return data;
}

export async function setAdGuardDnssec(enabled: boolean): Promise<DnssecStatus> {
  const { data } = await api.post<DnssecStatus>("/api/adguard/dnssec", {
    enabled,
  });
  return data;
}
