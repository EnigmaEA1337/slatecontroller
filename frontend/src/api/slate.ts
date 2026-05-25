import { api } from "./client";
import type { HardeningResponse } from "@/types/hardening";
import type { SlateStatus } from "@/types/slate";

export async function getSlateStatus(): Promise<SlateStatus> {
  const { data } = await api.get<SlateStatus>("/api/slate/status");
  return data;
}

export async function getSlateHardening(): Promise<HardeningResponse> {
  const { data } = await api.get<HardeningResponse>("/api/slate/hardening");
  return data;
}

// Touchscreen lock (PIN) — applies to the controller's default device.
// The PIN itself is never returned; only `pin_strength` computed server-side.
export type PinStrength = "none" | "weak" | "medium" | "strong";

export interface ScreenLockStatus {
  enabled: boolean;
  has_pin: boolean;
  pin_length: number;
  pin_strength: PinStrength;
  auto_lock_seconds: number;
}

export async function getScreenLock(): Promise<ScreenLockStatus> {
  const { data } = await api.get<ScreenLockStatus>("/api/slate/screen-lock");
  return data;
}

export async function setScreenLockPin(pin: string): Promise<ScreenLockStatus> {
  const { data } = await api.put<ScreenLockStatus>(
    "/api/slate/screen-lock/pin",
    { pin },
  );
  return data;
}

export async function setScreenLockEnabled(
  enabled: boolean,
): Promise<ScreenLockStatus> {
  const { data } = await api.put<ScreenLockStatus>(
    "/api/slate/screen-lock/enabled",
    { enabled },
  );
  return data;
}

export async function setScreenLockAutoLock(
  seconds: number,
): Promise<ScreenLockStatus> {
  const { data } = await api.put<ScreenLockStatus>(
    "/api/slate/screen-lock/auto-lock",
    { seconds },
  );
  return data;
}
