export type Severity = "critical" | "high" | "medium" | "low" | "unknown";

export interface SecurityPackage {
  name: string;
  version: string;
  upstream_version: string;
  vendor_specific: boolean;
}

export interface AttackPath {
  cwe: string[];
  capec: string[];
  techniques: string[]; // already "T1027" / "T1574.006" form
  atlas: string[];
}

export type ExploitMaturity =
  | "none"
  | "poc"
  | "functional"
  | "weaponized"
  | "in_the_wild";

export type PriorityLevel = "critical" | "high" | "medium" | "low" | "info";
export type AttackVector =
  | "network"
  | "adjacent"
  | "local"
  | "physical"
  | "unknown";

export interface KEV {
  date_added: string;
  due_date: string | null;
  vendor: string | null;
  product: string | null;
  vulnerability_name: string | null;
  short_description: string | null;
  required_action: string | null;
  known_ransomware_use: boolean;
}

export interface EPSS {
  score: number;
  percentile: number;
  date: string;
}

export interface ExploitSourceRef {
  source: string;
  url: string;
  title: string | null;
  author: string | null;
  date_published: string | null;
  verified: boolean;
  stars: number | null;
}

export interface CertFrBulletin {
  ref: string;
  kind: "alerte" | "avis";
  title: string;
  url: string;
  pub_date: string | null;
  actively_exploited: boolean;
  ransomware_mentioned: boolean;
}

export interface ExploitEnrichment {
  kev: KEV | null;
  epss: EPSS | null;
  exploit_db: ExploitSourceRef[];
  github_pocs: ExploitSourceRef[];
  metasploit_modules: ExploitSourceRef[];
  cert_fr: CertFrBulletin[];
  exploit_maturity: ExploitMaturity;
  priority_score: number;
  priority_level: PriorityLevel;
  last_refreshed_at: string | null;
}

export interface RiskAcceptance {
  accepted_by: string;
  accepted_at: string;
  reason: string;
  expires_at: string | null;
  expired: boolean;
}

export interface Finding {
  cve_id: string;
  package_name: string;
  package_version: string;
  severity: Severity;
  source: string;
  fixed_in: string | null;
  url: string | null;
  summary: string;
  cvss_score: number | null;
  cvss_vector: string | null;
  attack_vector: AttackVector;
  aliases: string[];
  attack_path: AttackPath | null;
  exploit: ExploitEnrichment | null;
  acknowledged: boolean;
  ack_note: string;
  risk_acceptance: RiskAcceptance | null;
}

export interface SourcesStatus {
  cisa_kev: { count: number; last_refreshed_at: string | null };
  exploit_db: { count: number; last_refreshed_at: string | null };
  metasploit: { count: number; last_refreshed_at: string | null };
  cert_fr: { count: number; last_refreshed_at: string | null };
}

export interface RiskScoreComponent {
  id: string;
  label: string;
  value: number;
  weight: number;
  contribution: number;
  detail: string;
  cve_ids: string[];
}

export interface RiskScore {
  score: number;
  level: PriorityLevel;
  snapshot_id: number | null;
  snapshot_taken_at: string | null;
  components: RiskScoreComponent[];
  risk_accepted_count: number;
  risk_accepted_unlimited: number;
  risk_accepted_limited: number;
  findings_total: number;
  explanation: string;
}

export interface SnapshotSummary {
  id: number;
  taken_at: string;
  openwrt_release: string;
  firmware_version: string;
  kernel: string;
  package_count: number;
  scan_status: string;
  scan_error: string;
}

export interface SnapshotDetail extends SnapshotSummary {
  openwrt_distrib_id: string;
  openwrt_target: string;
  openwrt_arch: string;
  openwrt_taints: string;
  board_name: string;
  hostname: string;
  model: string;
  packages: SecurityPackage[];
}

export interface FindingsResponse {
  snapshot: SnapshotSummary | null;
  severity_counts: Partial<Record<Severity, number>>;
  findings: Finding[];
  vendor_packages: number;
  scanned_packages: number;
}

export interface ScanResponse {
  snapshot_id: number;
  findings_count: number;
  status: string;
  error: string;
}
