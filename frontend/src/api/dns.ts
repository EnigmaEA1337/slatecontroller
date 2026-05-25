import { api } from "./client";
import type {
  DnsProvider,
  NetworkProtection,
  ProtectionRequest,
  ReapplyReport,
  SecurityLevel,
} from "@/types/dns";

export async function getDnsCatalog(filters?: {
  euOnly?: boolean;
  filterProfile?: string;
}): Promise<{ providers: DnsProvider[]; total: number }> {
  const params = new URLSearchParams();
  if (filters?.euOnly) params.set("eu_only", "true");
  if (filters?.filterProfile) params.set("filter_profile", filters.filterProfile);
  const query = params.toString();
  const { data } = await api.get<{ providers: DnsProvider[]; total: number }>(
    `/api/dns/catalog${query ? `?${query}` : ""}`,
  );
  return data;
}

export async function getSecurityLevels(): Promise<{ levels: SecurityLevel[] }> {
  const { data } = await api.get<{ levels: SecurityLevel[] }>(
    "/api/dns/security-levels",
  );
  return data;
}

export async function listProtections(): Promise<{
  protections: NetworkProtection[];
}> {
  const { data } = await api.get<{ protections: NetworkProtection[] }>(
    "/api/dns/protections",
  );
  return data;
}

export async function getProtection(
  networkSlug: string,
): Promise<NetworkProtection> {
  const { data } = await api.get<NetworkProtection>(
    `/api/dns/protections/${networkSlug}`,
  );
  return data;
}

export async function setProtection(
  networkSlug: string,
  body: ProtectionRequest,
): Promise<NetworkProtection> {
  const { data } = await api.put<NetworkProtection>(
    `/api/dns/protections/${networkSlug}`,
    body,
  );
  return data;
}

export async function removeProtection(networkSlug: string): Promise<void> {
  await api.delete(`/api/dns/protections/${networkSlug}`);
}

export async function reapplyAllProtections(): Promise<ReapplyReport> {
  const { data } = await api.post<ReapplyReport>(
    "/api/dns/protections/reapply",
  );
  return data;
}

// Partial update of a security level. Backend re-applies to all networks
// using this level automatically.
export interface LevelPatchBody {
  description?: string;
  default_provider_slug?: string;
  allowed_provider_slugs?: string[];
  adguard_filtering?: boolean;
  safe_browsing?: boolean;
  parental_control?: boolean;
  safe_search?: boolean;
  blocked_services?: string[];
  adguard_blocklist_slugs?: string[];
  require_dot?: boolean;
  require_dnssec?: boolean;
  eu_only?: boolean;
}

export interface LevelMutationResponse {
  level: SecurityLevel;
  reapply: { ok: boolean; applied: string[]; errors: string[] };
}

export async function patchSecurityLevel(
  slug: string,
  body: LevelPatchBody,
): Promise<LevelMutationResponse> {
  const { data } = await api.patch<LevelMutationResponse>(
    `/api/dns/security-levels/${slug}`,
    body,
  );
  return data;
}

export async function resetSecurityLevel(
  slug: string,
): Promise<LevelMutationResponse> {
  const { data } = await api.post<LevelMutationResponse>(
    `/api/dns/security-levels/${slug}/reset`,
  );
  return data;
}

// AdGuard authoritative blocked services list, used by the level editor
// multi-select. Slim shape (id + name only, no SVG icons).
export interface AdGuardBlockedService {
  id: string;
  name: string;
}

export async function getAdGuardBlockedServicesCatalog(): Promise<{
  services: AdGuardBlockedService[];
}> {
  const { data } = await api.get<{ services: AdGuardBlockedService[] }>(
    "/api/adguard/blocked-services/catalog",
  );
  return data;
}

// Anti-bypass: block TCP/853 LAN→WAN + activate GL.iNet *_drop_leaked_*.
export interface AntiBypassStatus {
  custom_block_dot_active: boolean;
  gl_rules_enabled: Record<string, boolean | null>;
  all_active: boolean;
  any_active: boolean;
}

export async function getAntiBypassStatus(): Promise<AntiBypassStatus> {
  const { data } = await api.get<AntiBypassStatus>(
    "/api/dns/anti-bypass/status",
  );
  return data;
}

export async function enableAntiBypass(): Promise<AntiBypassStatus> {
  const { data } = await api.post<AntiBypassStatus>(
    "/api/dns/anti-bypass/enable",
  );
  return data;
}

export async function disableAntiBypass(): Promise<AntiBypassStatus> {
  const { data } = await api.post<AntiBypassStatus>(
    "/api/dns/anti-bypass/disable",
  );
  return data;
}
