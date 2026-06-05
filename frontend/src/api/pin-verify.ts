// PIN verification client — wraps POST /api/slate/screen-lock/verify
// with first-class handling of the 423 (locked) response so callers can
// branch on lockout state without re-parsing axios errors.

import axios from "axios";

import { api } from "./client";

export interface VerifyPinResult {
  ok: boolean;
  failed_count: number;
  remaining_attempts: number;
  remaining_lock_s: number;
}

export interface LockoutError {
  locked: true;
  retry_after_s: number;
  failed_count?: number;
}

export type VerifyOutcome =
  | { kind: "ok"; result: VerifyPinResult }
  | { kind: "wrong"; result: VerifyPinResult }
  | { kind: "locked"; retry_after_s: number; failed_count?: number }
  | { kind: "error"; message: string };

/** Run a verification attempt. Returns a discriminated outcome rather
 *  than throwing for the locked / wrong-pin paths — those are normal
 *  control flow, not exceptions. Real network/server errors still come
 *  through the "error" branch. */
export async function verifyPin(
  pin: string,
  scope = "controller_verify",
): Promise<VerifyOutcome> {
  try {
    const { data } = await api.post<VerifyPinResult>(
      "/api/slate/screen-lock/verify",
      { pin, scope },
    );
    return data.ok
      ? { kind: "ok", result: data }
      : { kind: "wrong", result: data };
  } catch (err) {
    if (axios.isAxiosError(err)) {
      if (err.response?.status === 423) {
        const detail = err.response.data?.detail ?? {};
        return {
          kind: "locked",
          retry_after_s: Number(detail.retry_after_s ?? 60),
          failed_count: detail.failed_count,
        };
      }
      return {
        kind: "error",
        message:
          err.response?.data?.detail ??
          err.message ??
          "Échec inconnu",
      };
    }
    return { kind: "error", message: String(err) };
  }
}
