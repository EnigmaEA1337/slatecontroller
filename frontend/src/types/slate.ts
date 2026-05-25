// Mirrors backend `app.api.routes.slate.SlateStatus`. Keep in sync manually
// for now (OpenAPI-to-TS codegen is a Phase 2 nice-to-have).

export interface SlateStatus {
  connected: boolean;
  timestamp: string; // ISO 8601

  model: string | null;
  firmware_version: string | null;
  firmware_type: string | null;
  hostname: string | null;
  mac: string | null;
  country_code: string | null;
  cpu_count: number | null;

  uptime_seconds: number | null;
  memory_total_bytes: number | null;
  memory_free_bytes: number | null;
  memory_usage_percent: number | null;
  cpu_temperature_celsius: number | null;
  load_average_1m: number | null;
  load_average_5m: number | null;
  load_average_15m: number | null;
  lan_ip: string | null;

  connected_clients: number | null;
  wan_online: boolean | null;
  services: Record<string, boolean> | null;
}
