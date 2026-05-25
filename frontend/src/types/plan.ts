export type PlanSubsystem =
  | "vpn"
  | "dns"
  | "firewall"
  | "wifi"
  | "adguard"
  | "tor"
  | "tailscale"
  | "logging";

export type PlanActionKind = "rpc" | "uci" | "service" | "noop";
export type PlanReadiness = "ready" | "needs_probe" | "skipped" | "blocker";

export interface PlanStep {
  subsystem: PlanSubsystem;
  action_kind: PlanActionKind;
  summary: string;
  note: string;
  target_values: Record<string, unknown>;
  readiness: PlanReadiness;
}

export interface ActivationPlan {
  profile_name: string;
  step_count: number;
  has_blockers: boolean;
  steps: PlanStep[];
}
