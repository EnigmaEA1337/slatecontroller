// API client for /api/security/anti-theft — operator-controlled
// autonomous mode + auto-erase policy.

import { api } from "./client";

export type AntiTheftAction = "alert" | "soft_wipe";

export interface LockoutState {
  failed_count: number;
  locked_until: string | null;
  remaining_attempts: number;
  remaining_lock_s: number;
}

export interface TouchscreenLockout {
  continuous_errors: number;
  exceed_count: number;
  exceed_limit: boolean;
  last_polled_at: string | null;
  last_error: string;
}

export interface CombinedLockout {
  controller: LockoutState;
  touchscreen: TouchscreenLockout;
}

export interface AntiTheftConfig {
  autonomous_mode: boolean;
  failure_threshold: number;
  action: AntiTheftAction;
  notify_webhook_url: string;
  total_failures: number;
  last_action_at: string | null;
  last_action_kind: string;
  last_action_note: string;
  failures_until_trigger: number;
  lockout: LockoutState;
  touchscreen: TouchscreenLockout;
}

export async function getLockoutStatus(): Promise<CombinedLockout> {
  const { data } = await api.get<CombinedLockout>(
    "/api/security/anti-theft/lockout-status",
  );
  return data;
}

export interface AntiTheftUpsert {
  autonomous_mode: boolean;
  failure_threshold: number;
  action: AntiTheftAction;
  notify_webhook_url?: string;
}

export async function getAntiTheftConfig(): Promise<AntiTheftConfig> {
  const { data } = await api.get<AntiTheftConfig>("/api/security/anti-theft");
  return data;
}

export async function updateAntiTheftConfig(
  body: AntiTheftUpsert,
): Promise<AntiTheftConfig> {
  const { data } = await api.put<AntiTheftConfig>(
    "/api/security/anti-theft",
    body,
  );
  return data;
}

export async function resetAntiTheftCounter(): Promise<AntiTheftConfig> {
  const { data } = await api.post<AntiTheftConfig>(
    "/api/security/anti-theft/reset-counter",
  );
  return data;
}

export async function testAntiTheftAction(): Promise<{ summary: string }> {
  const { data } = await api.post<{ summary: string }>(
    "/api/security/anti-theft/test",
  );
  return data;
}

export const ACTION_META: Record<
  AntiTheftAction,
  { label: string; color: string; hint: string }
> = {
  alert: {
    label: "alert",
    color: "#60a5fa",
    hint: "Log critique + audit + webhook futur. Aucune donnée touchée.",
  },
  soft_wipe: {
    label: "soft_wipe",
    color: "#fbbf24",
    hint: "Tailscale logout + clear UCI wireguard/openvpn + PIN écrasé par random 8-digits. Récup via factory reset.",
  },
};
