export type HardeningCheckStatus = "ready" | "needs_probe" | "skipped" | "error";

export interface HardeningCheck {
  name: string;
  points: number;
  max_points: number;
  status: HardeningCheckStatus;
  note: string;
}

export interface HardeningResponse {
  score: number;
  max_score: number;
  percent: number;
  reachable: boolean;
  checks: HardeningCheck[];
}
