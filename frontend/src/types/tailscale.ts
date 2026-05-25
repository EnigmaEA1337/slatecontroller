export type TailscaleBackendState =
  | "NoState"
  | "NeedsLogin"
  | "NeedsMachineAuth"
  | "Stopped"
  | "Starting"
  | "Running";

export interface TailscalePeer {
  hostname: string;
  dns_name: string;
  tailscale_ips: string[];
  online: boolean;
  os: string;
  user: string;
  last_seen: string | null;
  primary_routes: string[];
  exit_node: boolean;
  exit_node_option: boolean;
}

export interface TailscaleStatus {
  installed: boolean;
  daemon_running: boolean;
  backend_state: TailscaleBackendState;
  auth_url: string | null;
  hostname: string;
  tailscale_ips: string[];
  tailnet: string;
  self_id: string;
  accept_routes: boolean;
  advertised_routes: string[];
  exit_node_enabled: boolean;
  use_exit_node: string;
  peers: TailscalePeer[];
  error: string;
}

export interface TailscaleConfigInput {
  auth_key?: string | null;
  hostname?: string | null;
  accept_routes: boolean;
  accept_dns: boolean;
  advertise_routes: string[];
  advertise_exit_node: boolean;
  exit_node: string;
  shields_up: boolean;
}

export interface TailscaleConnectResponse {
  success: boolean;
  status: TailscaleStatus;
  note: string;
  auth_url: string | null;
}

export interface TailscaleConfigSummary {
  has_auth_key: boolean;
  config: Partial<TailscaleConfigInput> | null;
  last_applied_at: string | null;
}

export type AuditSeverity = "critical" | "high" | "medium" | "low" | "info" | "pass";
export type AuditCheckStatus = "pass" | "fail" | "warn" | "info" | "skip";
export type AuditGrade = "A" | "B" | "C" | "D" | "F";

export interface AuditFinding {
  id: string;
  label: string;
  status: AuditCheckStatus;
  severity: AuditSeverity;
  evidence: string;
  recommendation: string | null;
}

export type HAFailsafeMode = "fail_open" | "keep";

export interface TailscaleHAState {
  enabled: boolean;
  candidates: string[];
  check_interval_seconds: number;
  failsafe_mode: HAFailsafeMode;
  last_action:
    | "set" | "noop" | "down" | "error" | "killswitch_open"
    | null;
  last_action_at: string | null;
  last_action_detail: string | null;
  last_target: string | null;
  last_switched_at: string | null;
}

export interface TailscaleHAConfigPatch {
  enabled?: boolean;
  candidates?: string[];
  check_interval_seconds?: number;
  failsafe_mode?: HAFailsafeMode;
}

export interface TailscaleAuditReport {
  score: number;
  grade: AuditGrade;
  pass_count: number;
  fail_count: number;
  warn_count: number;
  generated_at: string;
  raw_summary: {
    version: string;
    peers: number;
    self_ip: string[];
    tailnet: string;
  };
  findings: AuditFinding[];
}
