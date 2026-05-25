export interface AdGuardStatus {
  uci_enabled: boolean;
  init_running: boolean;
  web_ui_reachable: boolean;
  web_ui_url: string;
  protection_enabled: boolean | null;
  dns_port: number | null;
  version: string | null;
  http_port: number;
  error: string | null;
}

export interface AdGuardStats {
  num_dns_queries: number;
  num_blocked_filtering: number;
  num_replaced_safebrowsing: number;
  num_replaced_parental: number;
  avg_processing_time_ms: number;
  top_queried_domains: Record<string, number>[];
  top_blocked_domains: Record<string, number>[];
  top_clients: Record<string, number>[];
}

export interface AdGuardFilter {
  id: number;
  name: string;
  url: string;
  enabled: boolean;
  rules_count: number;
  last_updated: string | null;
}
