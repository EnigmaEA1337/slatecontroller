export type HardeningCheckStatus = "ready" | "needs_probe" | "skipped" | "error";

export interface HardeningCheck {
  name: string;
  points: number;
  max_points: number;
  status: HardeningCheckStatus;
  note: string;
  /** True when the backend knows an idempotent auto-fix for this check.
   *  The UI surfaces a Corriger button only when this is set. */
  fix_available?: boolean;
}

export interface HardeningResponse {
  score: number;
  max_score: number;
  percent: number;
  reachable: boolean;
  checks: HardeningCheck[];
}
