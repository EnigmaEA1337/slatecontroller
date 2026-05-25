/**
 * Aggregated Slate reliability % derived from the three security KPIs.
 *
 * Maths: simple average of the components that are *available*. Components
 * are normalised to a "higher = safer" 0-100 scale so the average is
 * semantically coherent:
 *   - hardening: already a posture % (0..100, higher = better).
 *   - vulnerabilities: risk score is "higher = worse"; we invert it
 *     (100 - risk_score) to express the same direction.
 *   - tailscale audit: score is "higher = better".
 *
 * Tailscale data is read from the cache only — the audit endpoint costs
 * ~5-10s of SSH on the Slate, so triggering it from the sidebar would be
 * obnoxious. It contributes only after the user has visited the audit page
 * (the data is then warm in React Query cache for 5 minutes).
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getRiskScore } from "@/api/security";
import { getSlateHardening } from "@/api/slate";
import type { TailscaleAuditReport } from "@/types/tailscale";

export type ReliabilityStatus = "green" | "orange" | "red" | "unknown";

export interface ReliabilityComponent {
  id: "hardening" | "vulnerabilities" | "tailscale";
  label: string;
  percent: number;
  available: boolean;
  detail?: string;
}

export interface SecurityReliability {
  percent: number | null;
  status: ReliabilityStatus;
  components: ReliabilityComponent[];
  loading: boolean;
}

export function statusFromPercent(p: number | null): ReliabilityStatus {
  if (p === null) return "unknown";
  // Thresholds chosen for the 3-color requirement (red/orange/green only).
  if (p >= 85) return "green";
  if (p >= 60) return "orange";
  return "red";
}

export function useSecurityReliability(): SecurityReliability {
  const hardeningQ = useQuery({
    queryKey: ["slate-hardening"],
    queryFn: getSlateHardening,
    staleTime: 30_000,
  });
  const riskQ = useQuery({
    queryKey: ["security", "risk-score"],
    queryFn: getRiskScore,
    staleTime: 30_000,
  });
  const qc = useQueryClient();
  // Cache-only read: never trigger a fetch from the hook (audit is expensive).
  const tailscaleAudit = qc.getQueryData<TailscaleAuditReport>([
    "tailscale", "audit",
  ]);

  const components: ReliabilityComponent[] = [
    {
      id: "hardening",
      label: "Hardening device",
      percent: hardeningQ.data?.percent ?? 0,
      available: !!hardeningQ.data,
      detail: hardeningQ.data
        ? `${hardeningQ.data.score}/${hardeningQ.data.max_score} pts`
        : undefined,
    },
    {
      id: "vulnerabilities",
      label: "Vulnérabilités",
      percent: riskQ.data ? Math.max(0, 100 - riskQ.data.score) : 0,
      available: !!riskQ.data,
      detail: riskQ.data
        ? `${riskQ.data.findings_total} findings · risk ${riskQ.data.score}`
        : undefined,
    },
    {
      id: "tailscale",
      label: "Tailscale audit",
      percent: tailscaleAudit?.score ?? 0,
      available: !!tailscaleAudit,
      detail: tailscaleAudit
        ? `grade ${tailscaleAudit.grade} · ${tailscaleAudit.fail_count} fail · ${tailscaleAudit.warn_count} warn`
        : "ouvrir la page Audit pour rafraîchir",
    },
  ];

  const usable = components.filter((c) => c.available);
  const percent =
    usable.length > 0
      ? Math.round(
          usable.reduce((sum, c) => sum + c.percent, 0) / usable.length,
        )
      : null;

  return {
    percent,
    status: statusFromPercent(percent),
    components,
    loading: hardeningQ.isLoading || riskQ.isLoading,
  };
}
