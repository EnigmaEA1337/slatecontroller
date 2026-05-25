export type FilterProfile = "none" | "malware" | "family" | "adblock" | "custom";
export type LogPolicy = "none" | "anonymized" | "24h" | "logged";
export type Intensity = "light" | "balanced" | "strict";
export type LevelIntensity = "light" | "balanced" | "strict" | "paranoid";

export interface DnsProvider {
  slug: string;
  name: string;
  organization: string;
  country: string;
  is_eu_based: boolean;
  ipv4_primary: string;
  ipv4_secondary: string;
  ipv6_primary: string;
  doh_url: string;
  dot_hostname: string;
  filter_profile: FilterProfile;
  log_policy: LogPolicy;
  supports_dnssec: boolean;
  recommended: boolean;
  intensity: Intensity;
  description: string;
}

export interface SecurityLevel {
  slug: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  default_provider_slug: string;
  allowed_provider_slugs: string[];
  adguard_filtering: boolean;
  safe_browsing: boolean;
  parental_control: boolean;
  safe_search: boolean;
  blocked_services: string[];
  adguard_blocklist_slugs: string[];
  require_dot: boolean;
  require_dnssec: boolean;
  eu_only: boolean;
  intensity: LevelIntensity;
}

export interface NetworkProtection {
  network_slug: string;
  network_display_name: string;
  network_cidr: string;
  level_slug: string;
  level_name: string;
  provider_slug: string;
  provider_name: string;
  provider_country: string;
  provider_eu_based: boolean;
  provider_filter_profile: string;
  upstream_transports: string[];
  adguard_client_name: string;
  created_at: string;
  updated_at: string;
}

export interface ProtectionRequest {
  level_slug: string;
  provider_slug?: string | null;
}

export interface ReapplyReport {
  ok: boolean;
  applied: string[];
  skipped: string[];
  errors: string[];
}
