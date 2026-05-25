import { api } from "./client";
import type {
  FindingsResponse,
  RiskScore,
  ScanResponse,
  SnapshotDetail,
  SnapshotSummary,
  SourcesStatus,
} from "@/types/security";

export async function listSnapshots(limit = 30): Promise<SnapshotSummary[]> {
  const { data } = await api.get<SnapshotSummary[]>("/api/security/snapshots", {
    params: { limit },
  });
  return data;
}

export async function getSnapshot(id: number): Promise<SnapshotDetail> {
  const { data } = await api.get<SnapshotDetail>(`/api/security/snapshots/${id}`);
  return data;
}

export async function getFindings(snapshotId?: number): Promise<FindingsResponse> {
  const { data } = await api.get<FindingsResponse>("/api/security/findings", {
    params: snapshotId ? { snapshot_id: snapshotId } : undefined,
  });
  return data;
}

// A scan can take several minutes (SBOM + per-CVE OSV fetches). Override the
// default 15s client timeout so the UI button reflects reality.
export async function triggerScan(): Promise<ScanResponse> {
  const { data } = await api.post<ScanResponse>(
    "/api/security/scan",
    undefined,
    { timeout: 10 * 60 * 1000 },
  );
  return data;
}

export async function acknowledgeFinding(
  cve_id: string,
  package_name: string,
  note = "",
): Promise<void> {
  await api.post("/api/security/findings/acknowledge", {
    cve_id,
    package_name,
    note,
  });
}

export async function unacknowledgeFinding(
  cve_id: string,
  package_name: string,
): Promise<void> {
  await api.delete("/api/security/findings/acknowledge", {
    params: { cve_id, package_name },
  });
}

export async function getSourcesStatus(): Promise<SourcesStatus> {
  const { data } = await api.get<SourcesStatus>("/api/security/sources/status");
  return data;
}

export async function getRiskScore(): Promise<RiskScore> {
  const { data } = await api.get<RiskScore>("/api/security/risk-score");
  return data;
}

export interface RiskScoreHistoryPoint {
  snapshot_id: number;
  taken_at: string;
  score: number;
  level: string;
  critical_exploitable: number;
  kev_count: number;
  weaponized_count: number;
  remote_critical: number;
  cert_fr_alertes: number;
}

export async function getRiskScoreHistory(
  limit = 30,
): Promise<RiskScoreHistoryPoint[]> {
  const { data } = await api.get<{ points: RiskScoreHistoryPoint[] }>(
    "/api/security/risk-score/history",
    { params: { limit } },
  );
  return data.points;
}

export async function refreshSources(): Promise<Record<string, number | string>> {
  const { data } = await api.post<Record<string, number | string>>(
    "/api/security/sources/refresh",
    undefined,
    { timeout: 5 * 60 * 1000 },
  );
  return data;
}

export async function acceptRisk(
  cve_id: string,
  package_name: string,
  reason: string,
  expires_at: string | null,
): Promise<void> {
  await api.post("/api/security/findings/accept-risk", {
    cve_id,
    package_name,
    reason,
    expires_at,
  });
}

export async function revokeRisk(
  cve_id: string,
  package_name: string,
): Promise<void> {
  await api.delete("/api/security/findings/accept-risk", {
    params: { cve_id, package_name },
  });
}
