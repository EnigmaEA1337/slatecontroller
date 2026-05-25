import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Cpu,
  Database,
  Download,
  ExternalLink,
  EyeOff,
  Filter as FilterIcon,
  Flame,
  Github,
  Globe,
  HandshakeIcon,
  HardDrive,
  Info,
  Package as PackageIcon,
  Plug,
  RefreshCw,
  ScanLine,
  Scale,
  Search,
  Shield,
  ShieldAlert,
  Siren,
  Skull,
  Target,
  Users,
  X,
  Zap,
} from "lucide-react";
import {
  acceptRisk,
  acknowledgeFinding,
  getFindings,
  getRiskScore,
  getRiskScoreHistory,
  getSnapshot,
  getSourcesStatus,
  listSnapshots,
  refreshSources,
  revokeRisk,
  triggerScan,
  unacknowledgeFinding,
} from "@/api/security";
import type { RiskScoreHistoryPoint } from "@/api/security";
import {
  AttackVectorDonut,
  ExploitMaturityBar,
  KevWeaponizedTrend,
  RiskAcceptanceDonut,
  RiskScoreTrend,
  SeverityDonut,
  Sparkline,
  TacticCoverageBar,
  TopPackagesBar,
} from "@/components/SecurityCharts";
import type {
  AttackPath,
  AttackVector,
  CertFrBulletin,
  ExploitEnrichment,
  ExploitMaturity,
  ExploitSourceRef,
  Finding,
  PriorityLevel,
  RiskAcceptance,
  Severity,
  SnapshotDetail,
} from "@/types/security";
import mitreData from "@/data/mitre_attack.json";
import { cn } from "@/lib/utils";
import { errorMessage, formatDate } from "@/lib/error-utils";
import { createPortal } from "react-dom";


const SEVERITY_ORDER: Severity[] = [
  "critical",
  "high",
  "medium",
  "low",
  "unknown",
];

const SEVERITY_STYLES: Record<Severity, string> = {
  critical:
    "border-red-500/60 bg-red-500/10 text-red-300 cyber-glow",
  high:
    "border-orange-500/60 bg-orange-500/10 text-orange-300",
  medium:
    "border-yellow-500/60 bg-yellow-500/10 text-yellow-200",
  low:
    "border-sky-500/40 bg-sky-500/10 text-sky-300",
  unknown:
    "border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] text-[color:var(--color-cyber-muted)]",
};

const SEVERITY_LABELS: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  unknown: "Unknown",
};

function SeverityBadge({ sev }: { sev: Severity }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 border px-2 py-[2px] text-[10px] font-bold uppercase tracking-[0.18em]",
        SEVERITY_STYLES[sev],
      )}
    >
      {SEVERITY_LABELS[sev]}
    </span>
  );
}


type TabId = "vulns" | "matrix" | "inventory" | "snapshots";

type VulnsPreset = "must_fix" | "critical_exploitable" | null;

/** A named set of CVE ids the user navigated to (e.g. "Weaponized (7)"). */
type CveSubset = { label: string; ids: string[] } | null;

export default function Vulnerabilities() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<TabId>("vulns");
  // Lifted filters so the matrix + risk-score breakdown can drive the other tabs.
  const [vulnsPreset, setVulnsPreset] = useState<VulnsPreset>("must_fix");
  const [techniqueFilter, setTechniqueFilter] = useState<string | null>(null);
  const [cveSubset, setCveSubset] = useState<CveSubset>(null);

  const findingsQ = useQuery({
    queryKey: ["security", "findings"],
    queryFn: () => getFindings(),
    refetchInterval: 30_000,
  });

  const snapshotsQ = useQuery({
    queryKey: ["security", "snapshots"],
    queryFn: () => listSnapshots(30),
    refetchInterval: 60_000,
  });

  const sourcesQ = useQuery({
    queryKey: ["security", "sources"],
    queryFn: getSourcesStatus,
    refetchInterval: 60_000,
  });

  const riskScoreQ = useQuery({
    queryKey: ["security", "risk-score"],
    queryFn: getRiskScore,
    refetchInterval: 60_000,
  });

  const scanMutation = useMutation({
    mutationFn: triggerScan,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["security"] });
    },
  });

  const refreshSourcesMutation = useMutation({
    mutationFn: refreshSources,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["security", "sources"] });
      qc.invalidateQueries({ queryKey: ["security", "findings"] });
    },
  });

  const snapshot = findingsQ.data?.snapshot ?? null;
  const counts = findingsQ.data?.severity_counts ?? {};
  const findings = findingsQ.data?.findings ?? [];

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <ScanLine className="cyber-glow h-5 w-5" />
          <h1 className="cyber-display cyber-glow text-2xl">
            SECURITY DEVICE STATUS
          </h1>
        </div>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          SBOM + match CVE pour le Slate. Source primaire : OSV.dev.
        </p>
      </div>

      {/* Top: device risk score + action bar */}
      {riskScoreQ.data && (
        <RiskScoreTile
          rs={riskScoreQ.data}
          findings={findings}
          onJumpToVulns={(label, ids) => {
            setCveSubset({ label, ids });
            setTechniqueFilter(null);
            setVulnsPreset(null);
            setTab("vulns");
          }}
          onJumpToMatrix={(label, ids) => {
            setCveSubset({ label, ids });
            setTechniqueFilter(null);
            setTab("matrix");
          }}
        />
      )}

      {/* Action bar + snapshot summary */}
      <div className="cyber-panel grid grid-cols-1 gap-4 p-5 md:grid-cols-[1fr_auto]">
        <div className="space-y-3 text-sm">
          {snapshot ? (
            <>
              <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                <Info label="OpenWrt" value={snapshot.openwrt_release || "—"} />
                <Info label="Firmware GL.iNet" value={snapshot.firmware_version || "—"} />
                <Info label="Kernel" value={snapshot.kernel || "—"} />
                <Info label="Paquets" value={String(snapshot.package_count)} />
              </div>
              <div className="text-[11px] text-[color:var(--color-cyber-muted)]">
                Dernier scan : {formatDate(snapshot.taken_at)} ·{" "}
                <ScanStatusBadge status={snapshot.scan_status} />
                {snapshot.scan_error && (
                  <span className="ml-2 text-red-300">{snapshot.scan_error}</span>
                )}
              </div>
            </>
          ) : (
            <div className="text-xs text-[color:var(--color-cyber-muted)]">
              Aucun snapshot. Lance un premier scan pour générer l'inventaire.
            </div>
          )}
        </div>
        <div className="flex flex-col items-stretch gap-2">
          <button
            type="button"
            onClick={() => scanMutation.mutate()}
            disabled={scanMutation.isPending}
            className={cn(
              "flex items-center justify-center gap-2 border px-4 py-2 text-[11px] font-bold uppercase tracking-[0.2em] transition",
              "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20",
              "disabled:opacity-50",
            )}
          >
            <RefreshCw
              className={cn(
                "h-3.5 w-3.5",
                scanMutation.isPending && "animate-spin",
              )}
            />
            {scanMutation.isPending ? "Scan en cours…" : "Lancer un scan"}
          </button>
          <button
            type="button"
            onClick={() => refreshSourcesMutation.mutate()}
            disabled={refreshSourcesMutation.isPending}
            className={cn(
              "flex items-center justify-center gap-2 border px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition",
              "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]",
              "disabled:opacity-50",
            )}
            title="Recharge KEV + Exploit-DB + Metasploit (sans toucher au Slate)"
          >
            <Database
              className={cn(
                "h-3 w-3",
                refreshSourcesMutation.isPending && "animate-spin",
              )}
            />
            {refreshSourcesMutation.isPending ? "Refresh…" : "Refresh sources"}
          </button>
          {scanMutation.isError && (
            <div className="text-[10px] text-red-300">
              {errorMessage(scanMutation.error)}
            </div>
          )}
          {scanMutation.isSuccess && !scanMutation.isPending && (
            <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
              Snapshot #{scanMutation.data.snapshot_id} —{" "}
              {scanMutation.data.findings_count} findings
            </div>
          )}
        </div>
      </div>

      {/* Sources status strip */}
      {sourcesQ.data && (
        <div className="cyber-panel flex flex-wrap items-center gap-4 px-4 py-2 text-[10px] text-[color:var(--color-cyber-muted)]">
          <span className="cyber-label text-[10px]">sources</span>
          <SourceStatLine
            label="CISA KEV"
            count={sourcesQ.data.cisa_kev.count}
            ts={sourcesQ.data.cisa_kev.last_refreshed_at}
          />
          <SourceStatLine
            label="Exploit-DB"
            count={sourcesQ.data.exploit_db.count}
            ts={sourcesQ.data.exploit_db.last_refreshed_at}
          />
          <SourceStatLine
            label="Metasploit"
            count={sourcesQ.data.metasploit.count}
            ts={sourcesQ.data.metasploit.last_refreshed_at}
          />
          <SourceStatLine
            label="CERT-FR (ANSSI)"
            count={sourcesQ.data.cert_fr.count}
            ts={sourcesQ.data.cert_fr.last_refreshed_at}
          />
          <span className="ml-auto text-[9px]">
            EPSS &amp; GitHub PoC : pull par CVE (cache 24h)
          </span>
        </div>
      )}

      {/* Severity counts */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        {SEVERITY_ORDER.map((sev) => (
          <SeverityCard key={sev} sev={sev} count={counts[sev] ?? 0} />
        ))}
      </div>

      {findingsQ.data && findingsQ.data.scanned_packages > 0 && (
        <div className="cyber-panel flex items-start gap-2 p-3 text-[11px] text-[color:var(--color-cyber-muted)]">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-yellow-300" />
          <div>
            <strong className="text-[color:var(--color-cyber-fg)]">
              {findingsQ.data.scanned_packages}
            </strong>{" "}
            paquets scannés via OSV.dev.{" "}
            <strong className="text-[color:var(--color-cyber-fg)]">
              {findingsQ.data.vendor_packages}
            </strong>{" "}
            paquets GL.iNet/vendor-specific non scannés (pas de couverture
            CVE upstream). OpenWrt backporte régulièrement des correctifs sans
            bumper la version → certains findings peuvent être déjà patchés.
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-[color:var(--color-cyber-border)]">
        {[
          { id: "vulns" as TabId, label: "Vulnérabilités" },
          { id: "matrix" as TabId, label: "Matrice ATT&CK" },
          { id: "inventory" as TabId, label: "Inventaire" },
          { id: "snapshots" as TabId, label: "Snapshots" },
        ].map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={cn(
              "px-4 py-2 text-[11px] font-bold uppercase tracking-[0.2em] transition",
              tab === t.id
                ? "cyber-glow border-b-2 border-[color:var(--color-cyber-accent)] text-[color:var(--color-cyber-accent)]"
                : "border-b-2 border-transparent text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {findingsQ.isLoading && (
        <div className="cyber-panel p-4 text-xs text-[color:var(--color-cyber-muted)]">
          Chargement…
        </div>
      )}
      {findingsQ.isError && (
        <div className="cyber-panel p-4 text-xs text-red-300">
          {errorMessage(findingsQ.error)}
        </div>
      )}

      {tab === "vulns" && (
        <VulnsTab
          findings={findings}
          preset={vulnsPreset}
          onPresetChange={setVulnsPreset}
          techniqueFilter={techniqueFilter}
          onClearTechniqueFilter={() => setTechniqueFilter(null)}
          cveSubset={cveSubset}
          onClearCveSubset={() => setCveSubset(null)}
        />
      )}
      {tab === "matrix" && (
        <MatrixTab
          findings={findings}
          cveSubset={cveSubset}
          onClearCveSubset={() => setCveSubset(null)}
          onJumpToVulnsTechnique={(t) => {
            setTechniqueFilter(t);
            setCveSubset(null);
            setVulnsPreset(null);
            setTab("vulns");
          }}
          onJumpToCriticalExploitable={() => {
            setVulnsPreset("critical_exploitable");
            setTechniqueFilter(null);
            setCveSubset(null);
            setTab("vulns");
          }}
        />
      )}
      {tab === "inventory" && (
        <InventoryTab snapshotId={snapshot?.id ?? null} />
      )}
      {tab === "snapshots" && (
        <SnapshotsTab
          snapshots={snapshotsQ.data ?? []}
          loading={snapshotsQ.isLoading}
        />
      )}
    </div>
  );
}

// ---------------------------- Sub-components ---------------------------- #

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="cyber-label mb-1 text-[10px]">{label}</div>
      <div className="font-mono text-xs text-[color:var(--color-cyber-fg)]">
        {value}
      </div>
    </div>
  );
}

function SourceStatLine({
  label,
  count,
  ts,
}: {
  label: string;
  count: number;
  ts: string | null;
}) {
  const loaded = count > 0;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1",
        loaded ? "text-[color:var(--color-cyber-fg)]" : "text-yellow-300",
      )}
    >
      <span className="cyber-label text-[9px]">{label}</span>
      <span className="font-mono">{loaded ? count : "—"}</span>
      {ts && (
        <span className="text-[9px] text-[color:var(--color-cyber-muted)]">
          ({formatDate(ts)})
        </span>
      )}
    </span>
  );
}

// Risk-score color ramp: 100 = pwned (red), 0 = clean (green).
// "info" is the lowest risk bucket — we want it green so the user reads
// "0/100 INFO" as "all clear", not as "neutral / unknown".
const PRIORITY_STYLES: Record<PriorityLevel, string> = {
  critical: "border-red-500/60 bg-red-500/15 text-red-300 cyber-glow",
  high: "border-orange-500/60 bg-orange-500/10 text-orange-300",
  medium: "border-yellow-500/60 bg-yellow-500/10 text-yellow-200",
  low: "border-lime-500/50 bg-lime-500/10 text-lime-300",
  info: "border-emerald-500/60 bg-emerald-500/10 text-emerald-300",
};

function PriorityBadge({ score, level }: { score: number; level: PriorityLevel }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 border px-2 py-[2px] font-mono text-[10px] uppercase tracking-[0.18em]",
        PRIORITY_STYLES[level],
      )}
      title={`Score composite CVSS + EPSS + KEV + maturity`}
    >
      {score.toFixed(0)}
      <span className="opacity-70">/100</span>
    </span>
  );
}

const MATURITY_STYLES: Record<ExploitMaturity, string> = {
  none: "text-[color:var(--color-cyber-muted)] border-[color:var(--color-cyber-border)]",
  poc: "text-sky-300 border-sky-500/40",
  functional: "text-yellow-200 border-yellow-500/50",
  weaponized: "text-orange-300 border-orange-500/60",
  in_the_wild: "text-red-300 border-red-500/60 bg-red-500/10 cyber-glow",
};

const MATURITY_LABELS: Record<ExploitMaturity, string> = {
  none: "no exploit",
  poc: "PoC",
  functional: "functional",
  weaponized: "weaponized",
  in_the_wild: "in the wild",
};

function RiskScoreTile({
  rs,
  findings,
  onJumpToVulns,
  onJumpToMatrix,
}: {
  rs: import("@/types/security").RiskScore;
  findings: Finding[];
  onJumpToVulns: (label: string, ids: string[]) => void;
  onJumpToMatrix: (label: string, ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);

  // Sparkline history per component — same endpoint we use for trends.
  // Loaded lazily when the breakdown is opened to avoid the cost on every
  // page render.
  const historyQ = useQuery({
    queryKey: ["security", "risk-score-history"],
    queryFn: () => getRiskScoreHistory(30),
    enabled: open,
    staleTime: 60_000,
  });
  const componentSparks = useMemo(() => {
    if (!historyQ.data) return null;
    const get = (id: string, pt: RiskScoreHistoryPoint): number => {
      switch (id) {
        case "critical_exploitable":
          return pt.critical_exploitable;
        case "in_the_wild":
          return pt.kev_count;
        case "weaponized":
          return pt.weaponized_count;
        case "remote_critical":
          return pt.remote_critical;
        case "cert_fr_alerte":
          return pt.cert_fr_alertes;
        case "priority_avg":
          return pt.score;
        default:
          return 0;
      }
    };
    const out: Record<string, number[]> = {};
    for (const c of rs.components) {
      out[c.id] = historyQ.data.map((p) => get(c.id, p));
    }
    return out;
  }, [historyQ.data, rs.components]);
  // Same ramp as PRIORITY_STYLES — 0 = green (excellent), 100 = red (critical).
  const levelCls: Record<PriorityLevel, string> = {
    critical: "border-red-500/70 bg-red-500/15 text-red-300 cyber-glow",
    high: "border-orange-500/60 bg-orange-500/10 text-orange-300",
    medium: "border-yellow-500/60 bg-yellow-500/10 text-yellow-200",
    low: "border-lime-500/50 bg-lime-500/10 text-lime-300",
    info: "border-emerald-500/60 bg-emerald-500/10 text-emerald-300 cyber-glow-ok",
  };
  // Color for the big score number itself. cyber-glow's default red would
  // make "0/100 INFO" still read red — use a level-aware text color instead.
  const scoreNumCls: Record<PriorityLevel, string> = {
    critical: "cyber-glow text-red-300",
    high: "text-orange-300",
    medium: "text-yellow-200",
    low: "text-lime-300",
    info: "cyber-glow-ok",
  };
  return (
    <div
      className={cn(
        "cyber-panel border-l-4 p-4 transition-all",
        levelCls[rs.level],
      )}
    >
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-baseline gap-2">
          <span className="cyber-label text-[10px]">device risk</span>
          <span
            className={cn(
              "cyber-display text-3xl font-extrabold",
              scoreNumCls[rs.level],
            )}
          >
            {rs.score.toFixed(0)}
          </span>
          <span className="text-sm opacity-70">/100</span>
          <span
            className={cn(
              "ml-2 inline-flex items-center border px-2 py-[2px] text-[10px] font-bold uppercase tracking-[0.18em]",
              levelCls[rs.level],
            )}
          >
            {rs.level}
          </span>
        </div>
        <div className="flex-1 text-[11px] text-[color:var(--color-cyber-muted)]">
          {rs.explanation}
          {rs.snapshot_taken_at && (
            <div className="mt-0.5 text-[9px]">
              calculé sur snap #{rs.snapshot_id} ({formatDate(rs.snapshot_taken_at)})
              · {rs.findings_total} findings au total
              · {rs.risk_accepted_count} risque(s) accepté(s) exclu(s)
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
        >
          {open ? (
            <>
              <ChevronDown className="h-3 w-3" /> Replier breakdown
            </>
          ) : (
            <>
              <ChevronRight className="h-3 w-3" /> Voir breakdown
            </>
          )}
        </button>
      </div>
      {rs.risk_accepted_count > 0 && (
        <div className="mt-3 flex flex-wrap items-start gap-3">
          <div className="max-w-xs">
            <RiskAcceptanceDonut rs={rs} />
          </div>
          <button
            type="button"
            onClick={() => setReviewOpen(true)}
            className="inline-flex items-center gap-1 border border-amber-500/60 bg-amber-500/10 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-amber-300 hover:bg-amber-500/20"
            title="Lister, exporter, et révoquer les risques acceptés"
          >
            <Scale className="h-3 w-3" />
            Revue des risques acceptés ({rs.risk_accepted_count})
          </button>
        </div>
      )}
      {reviewOpen && (
        <RiskAcceptanceReview
          findings={findings}
          onClose={() => setReviewOpen(false)}
        />
      )}
      {open && (
        <div className="mt-3 space-y-1">
          {rs.components.map((c) => {
            const ratio = c.weight > 0 ? c.contribution / c.weight : 0;
            const hasCves = c.cve_ids.length > 0;
            return (
              <div
                key={c.id}
                className="grid grid-cols-[1fr_auto_90px_120px_auto] items-center gap-3 text-[10px]"
              >
                <div>
                  <div className="text-[color:var(--color-cyber-fg)]">{c.label}</div>
                  <div className="text-[9px] text-[color:var(--color-cyber-muted)]">
                    {c.detail}
                  </div>
                </div>
                <div className="font-mono">
                  valeur{" "}
                  <span className="text-[color:var(--color-cyber-fg)]">
                    {typeof c.value === "number" && Number.isInteger(c.value)
                      ? c.value
                      : c.value.toFixed(1)}
                  </span>
                </div>
                <div className="flex items-center">
                  {componentSparks && componentSparks[c.id] ? (
                    <Sparkline
                      values={componentSparks[c.id]}
                      color={
                        ratio >= 0.66
                          ? "#f87171"
                          : ratio >= 0.33
                          ? "#fde047"
                          : "#7dd3fc"
                      }
                      height={20}
                      width={80}
                    />
                  ) : (
                    <span className="text-[9px] text-[color:var(--color-cyber-muted)]">
                      …
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <div className="h-1.5 w-full bg-[color:var(--color-cyber-surface)]">
                    <div
                      className={cn(
                        "h-full",
                        ratio >= 0.66
                          ? "bg-red-400"
                          : ratio >= 0.33
                          ? "bg-yellow-300"
                          : "bg-sky-300",
                      )}
                      style={{ width: `${Math.min(ratio * 100, 100)}%` }}
                    />
                  </div>
                  <span className="ml-1 w-12 text-right font-mono">
                    {c.contribution.toFixed(1)}/{c.weight}
                  </span>
                </div>
                <div className="flex items-center gap-1">
                  {hasCves ? (
                    <>
                      <button
                        type="button"
                        onClick={() => onJumpToVulns(c.label, c.cve_ids)}
                        className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-[2px] text-[9px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
                        title={`Filtrer Vulnérabilités sur ces ${c.cve_ids.length} CVE`}
                      >
                        <Shield className="h-3 w-3" />
                        vulns
                      </button>
                      <button
                        type="button"
                        onClick={() => onJumpToMatrix(c.label, c.cve_ids)}
                        className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-[2px] text-[9px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
                        title={`Voir uniquement les techniques ATT&CK touchées par ces ${c.cve_ids.length} CVE`}
                      >
                        <Target className="h-3 w-3" />
                        matrice
                      </button>
                    </>
                  ) : (
                    <span className="text-[9px] text-[color:var(--color-cyber-muted)]">
                      —
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function MaturityBadge({ m }: { m: ExploitMaturity }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 border px-1.5 py-[1px] text-[10px] uppercase tracking-[0.18em]",
        MATURITY_STYLES[m],
      )}
    >
      {m === "in_the_wild" && <Flame className="h-3 w-3" />}
      {m === "weaponized" && <Skull className="h-3 w-3" />}
      {MATURITY_LABELS[m]}
    </span>
  );
}

const AV_META: Record<
  AttackVector,
  { label: string; cls: string; icon: typeof Globe; hint: string }
> = {
  network: {
    label: "remote",
    cls: "border-red-500/50 bg-red-500/10 text-red-300",
    icon: Globe,
    hint: "AV:N — exploitable depuis le réseau, pas besoin d'accès local",
  },
  adjacent: {
    label: "adjacent",
    cls: "border-orange-500/40 bg-orange-500/10 text-orange-300",
    icon: Users,
    hint: "AV:A — même segment réseau / Bluetooth / Wi-Fi proximité",
  },
  local: {
    label: "local",
    cls: "border-yellow-500/40 bg-yellow-500/10 text-yellow-200",
    icon: HardDrive,
    hint: "AV:L — attaquant doit déjà avoir un shell ou contrôler un compte sur la machine",
  },
  physical: {
    label: "physical",
    cls: "border-sky-500/40 bg-sky-500/10 text-sky-300",
    icon: Plug,
    hint: "AV:P — accès physique requis (USB, console série, ...)",
  },
  unknown: {
    label: "?",
    cls: "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]",
    icon: Info,
    hint: "Pas de vecteur CVSS dans les sources",
  },
};

function AttackVectorBadge({ av }: { av: AttackVector }) {
  const m = AV_META[av];
  const Icon = m.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 border px-1.5 py-[1px] text-[10px] uppercase tracking-[0.18em]",
        m.cls,
      )}
      title={m.hint}
    >
      <Icon className="h-3 w-3" />
      {m.label}
    </span>
  );
}

function isRiskAccepted(f: Finding): boolean {
  // Active risk acceptance = present + not expired.
  return !!f.risk_acceptance && !f.risk_acceptance.expired;
}

// "Critical exploitable": EPSS ≥ 0.7 OR in KEV, AND has a real exploit
// (Metasploit module, Exploit-DB entry, or maturity functional/weaponized/
// in_the_wild). Active risk acceptances excluded. This is the "things that
// will get hit first" preset.
function isCriticalExploitable(f: Finding): boolean {
  if (isRiskAccepted(f)) return false;
  const exp = f.exploit;
  if (!exp) return false;
  const epssHigh = (exp.epss?.score ?? 0) >= 0.7;
  const inKev = !!exp.kev;
  if (!epssHigh && !inKev) return false;
  const hasRealExploit =
    exp.metasploit_modules.length > 0 ||
    exp.exploit_db.length > 0 ||
    exp.exploit_maturity === "functional" ||
    exp.exploit_maturity === "weaponized" ||
    exp.exploit_maturity === "in_the_wild";
  return hasRealExploit;
}

// "Must-fix" view: severity ∈ {critical, high} AND (CVSS ≥ 7 OR in KEV
// OR exploit public OR CERT-FR alerte). Floor of "things you cannot ignore"
// — both impact AND a credible exploitability signal.
// Active risk acceptances are hidden in this view (the user explicitly
// declared "I live with it"). Expired acceptances reappear here.
function isMustFix(f: Finding): boolean {
  if (isRiskAccepted(f)) return false;
  const sevHigh = f.severity === "critical" || f.severity === "high";
  if (!sevHigh) return false;
  const cvssHigh = (f.cvss_score ?? 0) >= 7;
  const inKev = !!f.exploit?.kev;
  const inCertFrAlerte =
    !!f.exploit?.cert_fr.some((b) => b.kind === "alerte");
  const hasExploit =
    !!f.exploit &&
    (f.exploit.metasploit_modules.length > 0 ||
      f.exploit.exploit_db.length > 0 ||
      f.exploit.github_pocs.length > 0 ||
      f.exploit.exploit_maturity === "weaponized" ||
      f.exploit.exploit_maturity === "in_the_wild" ||
      f.exploit.exploit_maturity === "functional");
  return cvssHigh || inKev || hasExploit || inCertFrAlerte;
}

function CertFrBadge({
  alerteCount,
  avisCount,
}: {
  alerteCount: number;
  avisCount: number;
}) {
  if (alerteCount === 0 && avisCount === 0) return null;
  const hasAlerte = alerteCount > 0;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 border px-1.5 py-[1px] text-[10px] uppercase tracking-[0.18em]",
        hasAlerte
          ? "border-blue-500/60 bg-blue-500/10 text-blue-300 cyber-glow"
          : "border-blue-500/40 text-blue-300/80",
      )}
      title={
        hasAlerte
          ? `${alerteCount} alerte(s) ANSSI · ${avisCount} avis`
          : `${avisCount} avis ANSSI`
      }
    >
      🇫🇷 {hasAlerte ? `ALE×${alerteCount}` : `AVI×${avisCount}`}
    </span>
  );
}

function ScanStatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    scanned: "border-emerald-500/40 text-emerald-300",
    partial: "border-yellow-500/40 text-yellow-200",
    error: "border-red-500/40 text-red-300",
    pending: "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]",
  };
  return (
    <span
      className={cn(
        "inline-block border px-2 py-[1px] text-[10px] uppercase tracking-[0.18em]",
        map[status] ?? map.pending,
      )}
    >
      {status}
    </span>
  );
}

function SeverityCard({ sev, count }: { sev: Severity; count: number }) {
  return (
    <div
      className={cn(
        "cyber-panel flex flex-col gap-1 border-l-4 p-3",
        sev === "critical" && "border-l-red-500",
        sev === "high" && "border-l-orange-500",
        sev === "medium" && "border-l-yellow-500",
        sev === "low" && "border-l-sky-500",
        sev === "unknown" && "border-l-[color:var(--color-cyber-border)]",
      )}
    >
      <div className="cyber-label text-[10px]">{SEVERITY_LABELS[sev]}</div>
      <div className="cyber-display cyber-glow text-2xl font-extrabold">
        {count}
      </div>
    </div>
  );
}

type AvFilter = "all" | AttackVector;

function VulnsTab({
  findings,
  preset,
  onPresetChange,
  techniqueFilter,
  onClearTechniqueFilter,
  cveSubset,
  onClearCveSubset,
}: {
  findings: Finding[];
  preset: VulnsPreset;
  onPresetChange: (p: VulnsPreset) => void;
  techniqueFilter: string | null;
  onClearTechniqueFilter: () => void;
  cveSubset: CveSubset;
  onClearCveSubset: () => void;
}) {
  const qc = useQueryClient();
  const mustFixOnly = preset === "must_fix";
  const criticalExploitableOnly = preset === "critical_exploitable";
  // Build the lookup set once.
  const subsetSet = useMemo(
    () => (cveSubset ? new Set(cveSubset.ids) : null),
    [cveSubset],
  );
  const [filter, setFilter] = useState<Severity | "all">("all");
  const [avFilter, setAvFilter] = useState<AvFilter>("all");
  const [kevOnly, setKevOnly] = useState(false);
  const [anssiOnly, setAnssiOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [showAcked, setShowAcked] = useState(false);

  const ackMutation = useMutation({
    mutationFn: ({ f, ack }: { f: Finding; ack: boolean }) =>
      ack
        ? acknowledgeFinding(f.cve_id, f.package_name)
        : unacknowledgeFinding(f.cve_id, f.package_name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["security", "findings"] }),
  });

  const acceptRiskMutation = useMutation({
    mutationFn: ({
      f,
      reason,
      expires_at,
    }: {
      f: Finding;
      reason: string;
      expires_at: string | null;
    }) => acceptRisk(f.cve_id, f.package_name, reason, expires_at),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["security", "findings"] }),
  });

  const revokeRiskMutation = useMutation({
    mutationFn: (f: Finding) => revokeRisk(f.cve_id, f.package_name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["security", "findings"] }),
  });

  const [riskModalFor, setRiskModalFor] = useState<Finding | null>(null);
  // In "Tout afficher" mode, default to also showing risk-accepted findings.
  // User can hide them when they want to focus on un-triaged items.
  const [showAcceptedRisks, setShowAcceptedRisks] = useState(true);

  const mustFixCount = useMemo(
    () => findings.filter(isMustFix).length,
    [findings],
  );
  const criticalExploitableCount = useMemo(
    () => findings.filter(isCriticalExploitable).length,
    [findings],
  );

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    return findings
      .filter((f) =>
        mustFixOnly
          ? isMustFix(f)
          : criticalExploitableOnly
          ? isCriticalExploitable(f)
          : true,
      )
      .filter(
        (f) =>
          !techniqueFilter ||
          (f.attack_path?.techniques ?? []).includes(techniqueFilter),
      )
      .filter((f) => !subsetSet || subsetSet.has(f.cve_id))
      .filter((f) => filter === "all" || f.severity === filter)
      .filter((f) => avFilter === "all" || f.attack_vector === avFilter)
      .filter((f) => !kevOnly || !!f.exploit?.kev)
      .filter(
        (f) =>
          !anssiOnly ||
          !!f.exploit?.cert_fr.some((b) => b.kind === "alerte"),
      )
      .filter((f) => showAcked || !f.acknowledged)
      // Risk acceptances (active) are excluded from must-fix/critical presets
      // by the helpers above. In "Tout" mode, the showAcceptedRisks toggle
      // decides (default: shown).
      .filter(
        (f) =>
          mustFixOnly ||
          criticalExploitableOnly ||
          showAcceptedRisks ||
          !isRiskAccepted(f),
      )
      .filter(
        (f) =>
          !q ||
          f.cve_id.toLowerCase().includes(q) ||
          f.package_name.toLowerCase().includes(q) ||
          f.aliases.some((a) => a.toLowerCase().includes(q)),
      )
      .sort(
        (a, b) =>
          (b.exploit?.priority_score ?? 0) - (a.exploit?.priority_score ?? 0),
      );
  }, [
    findings,
    mustFixOnly,
    criticalExploitableOnly,
    techniqueFilter,
    subsetSet,
    filter,
    avFilter,
    kevOnly,
    anssiOnly,
    search,
    showAcked,
    showAcceptedRisks,
  ]);

  if (findings.length === 0) {
    return (
      <div className="cyber-panel p-6 text-center text-sm text-[color:var(--color-cyber-muted)]">
        Aucune vulnérabilité scannée. Lance un scan pour démarrer.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Distribution charts header — applies to the *unfiltered* set so
          users see the global picture, not what the filters whittled down to. */}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <SeverityDonut findings={findings} />
        <AttackVectorDonut findings={findings} />
        <ExploitMaturityBar findings={findings} />
      </div>

      {/* Top filter bar: preset selectors + KEV-only + search */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => onPresetChange("must_fix")}
          className={cn(
            "inline-flex items-center gap-1 border px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition",
            mustFixOnly
              ? "cyber-glow border-red-500/60 bg-red-500/10 text-red-300"
              : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
          )}
          title="severity critical/high ET (KEV OU exploit public OU CERT-FR alerte OU CVSS ≥ 7)"
        >
          <Siren className="h-3 w-3" />
          Must-fix
          <span className="ml-1 rounded-sm bg-red-500/20 px-1 font-mono">
            {mustFixCount}
          </span>
        </button>
        <button
          type="button"
          onClick={() => onPresetChange("critical_exploitable")}
          className={cn(
            "inline-flex items-center gap-1 border px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition",
            criticalExploitableOnly
              ? "cyber-glow border-orange-500/60 bg-orange-500/10 text-orange-300"
              : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
          )}
          title="EPSS ≥ 0.7 OU dans KEV, ET au moins un exploit publique (MSF/EDB/weaponized/in_the_wild)"
        >
          <Zap className="h-3 w-3" />
          Critique + exploit
          <span className="ml-1 rounded-sm bg-orange-500/20 px-1 font-mono">
            {criticalExploitableCount}
          </span>
        </button>
        <button
          type="button"
          onClick={() => onPresetChange(null)}
          className={cn(
            "border px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition",
            !mustFixOnly && !criticalExploitableOnly
              ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 text-[color:var(--color-cyber-accent)]"
              : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
          )}
        >
          Tout afficher
          <span className="ml-1 font-mono text-[color:var(--color-cyber-muted)]">
            ({findings.length})
          </span>
        </button>
        {techniqueFilter && (
          <span className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)]">
            <Target className="h-3 w-3" />
            Technique : {techniqueFilter}
            <button
              type="button"
              onClick={onClearTechniqueFilter}
              className="ml-1 hover:text-[color:var(--color-cyber-fg)]"
              aria-label="Retirer le filtre"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        )}
        {cveSubset && (
          <span className="inline-flex items-center gap-1 border border-orange-500/60 bg-orange-500/10 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-orange-300">
            <Zap className="h-3 w-3" />
            Sélection : {cveSubset.label} ({cveSubset.ids.length})
            <button
              type="button"
              onClick={onClearCveSubset}
              className="ml-1 hover:text-[color:var(--color-cyber-fg)]"
              aria-label="Retirer la sélection"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        )}
        <label className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          <input
            type="checkbox"
            checked={kevOnly}
            onChange={(e) => setKevOnly(e.target.checked)}
            className="accent-[color:var(--color-cyber-accent)]"
          />
          KEV uniquement
        </label>
        <label className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          <input
            type="checkbox"
            checked={anssiOnly}
            onChange={(e) => setAnssiOnly(e.target.checked)}
            className="accent-[color:var(--color-cyber-accent)]"
          />
          ANSSI alerte
        </label>
        <div className="flex items-center gap-1 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1">
          <Search className="h-3.5 w-3.5 text-[color:var(--color-cyber-muted)]" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="CVE, paquet, alias…"
            className="bg-transparent text-xs text-[color:var(--color-cyber-fg)] outline-none placeholder:text-[color:var(--color-cyber-muted)]"
          />
        </div>
        <label className="ml-auto flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          <input
            type="checkbox"
            checked={showAcked}
            onChange={(e) => setShowAcked(e.target.checked)}
            className="accent-[color:var(--color-cyber-accent)]"
          />
          Inclure les acked
        </label>
        {!mustFixOnly && !criticalExploitableOnly && (
          <label className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
            <input
              type="checkbox"
              checked={showAcceptedRisks}
              onChange={(e) => setShowAcceptedRisks(e.target.checked)}
              className="accent-[color:var(--color-cyber-accent)]"
            />
            Risques acceptés
          </label>
        )}
        <span className="text-[10px] text-[color:var(--color-cyber-muted)]">
          {filtered.length} / {findings.length}
        </span>
      </div>

      {/* Secondary filter rows */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          <FilterIcon className="h-3.5 w-3.5 text-[color:var(--color-cyber-muted)]" />
          <span className="cyber-label mr-1 text-[9px]">sev</span>
          {(["all", ...SEVERITY_ORDER] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setFilter(s)}
              className={cn(
                "border px-2 py-1 text-[10px] font-bold uppercase tracking-[0.15em] transition",
                filter === s
                  ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 text-[color:var(--color-cyber-accent)]"
                  : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
              )}
            >
              {s === "all" ? "tous" : SEVERITY_LABELS[s]}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <span className="cyber-label ml-2 mr-1 text-[9px]">attack vector</span>
          {(["all", "network", "adjacent", "local", "physical", "unknown"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setAvFilter(v)}
              className={cn(
                "border px-2 py-1 text-[10px] font-bold uppercase tracking-[0.15em] transition",
                avFilter === v
                  ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 text-[color:var(--color-cyber-accent)]"
                  : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
              )}
              title={v === "all" ? "Tous les vecteurs" : AV_META[v as AttackVector].hint}
            >
              {v === "all" ? "tous" : AV_META[v as AttackVector].label}
            </button>
          ))}
        </div>
      </div>

      <div className="cyber-panel overflow-hidden">
        <table className="w-full text-xs">
          <thead className="border-b border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]">
            <tr>
              <th className="w-6 px-2 py-2"></th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Priority</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Sev</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">CVSS</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Vector</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">CVE</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Paquet</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Version</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Fix</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Exploits</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Attack</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((f) => (
              <FindingRow
                key={`${f.cve_id}|${f.package_name}`}
                f={f}
                onToggleAck={() => ackMutation.mutate({ f, ack: !f.acknowledged })}
                onOpenRiskModal={(target) => setRiskModalFor(target)}
                onRevokeRisk={(target) => revokeRiskMutation.mutate(target)}
              />
            ))}
          </tbody>
        </table>
      </div>

      {riskModalFor && (
        <RiskAcceptModal
          finding={riskModalFor}
          onClose={() => setRiskModalFor(null)}
          onSubmit={(reason, expires_at) => {
            acceptRiskMutation.mutate(
              { f: riskModalFor, reason, expires_at },
              { onSuccess: () => setRiskModalFor(null) },
            );
          }}
          submitting={acceptRiskMutation.isPending}
          error={
            acceptRiskMutation.isError ? errorMessage(acceptRiskMutation.error) : null
          }
        />
      )}
    </div>
  );
}

function RiskAcceptanceReview({
  findings,
  onClose,
}: {
  findings: Finding[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const revoke = useMutation({
    mutationFn: (f: Finding) => revokeRisk(f.cve_id, f.package_name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["security"] }),
  });
  const accepted = useMemo(
    () => findings.filter((f) => f.risk_acceptance),
    [findings],
  );
  // Sort: expired first (action needed), then by accepted_at desc.
  const sorted = useMemo(
    () =>
      [...accepted].sort((a, b) => {
        const ae = a.risk_acceptance!.expired ? 1 : 0;
        const be = b.risk_acceptance!.expired ? 1 : 0;
        if (ae !== be) return be - ae;
        return (
          new Date(b.risk_acceptance!.accepted_at).getTime() -
          new Date(a.risk_acceptance!.accepted_at).getTime()
        );
      }),
    [accepted],
  );

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="cyber-panel w-full max-w-5xl space-y-3 border border-amber-500/40 bg-[color:var(--color-cyber-bg-2)] p-5">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="cyber-display cyber-glow text-lg text-amber-300">
              Revue des risques acceptés
            </h3>
            <p className="text-[11px] text-[color:var(--color-cyber-muted)]">
              {accepted.length} décision(s) de risque actives ou expirées.
              Une fois expirée, la vuln remonte automatiquement dans Must-fix.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
            aria-label="Fermer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {accepted.length === 0 ? (
          <div className="cyber-panel p-4 text-xs text-[color:var(--color-cyber-muted)]">
            Aucun risque accepté pour l'instant.
          </div>
        ) : (
          <div className="cyber-panel max-h-[60vh] overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 border-b border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]">
                <tr>
                  <th className="cyber-label px-3 py-2 text-left text-[10px]">État</th>
                  <th className="cyber-label px-3 py-2 text-left text-[10px]">CVE</th>
                  <th className="cyber-label px-3 py-2 text-left text-[10px]">Paquet</th>
                  <th className="cyber-label px-3 py-2 text-left text-[10px]">Priority</th>
                  <th className="cyber-label px-3 py-2 text-left text-[10px]">Accepté par</th>
                  <th className="cyber-label px-3 py-2 text-left text-[10px]">Le</th>
                  <th className="cyber-label px-3 py-2 text-left text-[10px]">Expire</th>
                  <th className="cyber-label px-3 py-2 text-left text-[10px]">Raison</th>
                  <th className="cyber-label px-3 py-2 text-left text-[10px]">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((f) => {
                  const r = f.risk_acceptance!;
                  return (
                    <tr
                      key={`${f.cve_id}|${f.package_name}`}
                      className={cn(
                        "border-b border-[color:var(--color-cyber-border)]/40",
                        r.expired && "bg-red-500/5",
                      )}
                    >
                      <td className="px-3 py-2">
                        {r.expired ? (
                          <span className="border border-red-500/60 px-1.5 py-[1px] text-[10px] uppercase tracking-[0.18em] text-red-300">
                            expiré
                          </span>
                        ) : r.expires_at ? (
                          <span className="border border-yellow-500/50 px-1.5 py-[1px] text-[10px] uppercase tracking-[0.18em] text-yellow-200">
                            actif
                          </span>
                        ) : (
                          <span className="border border-amber-500/60 px-1.5 py-[1px] text-[10px] uppercase tracking-[0.18em] text-amber-300">
                            permanent
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <a
                          href={
                            f.url ?? `https://nvd.nist.gov/vuln/detail/${f.cve_id}`
                          }
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-mono text-[color:var(--color-cyber-accent)] hover:underline"
                        >
                          {f.cve_id}
                        </a>
                      </td>
                      <td className="px-3 py-2 font-mono">
                        {f.package_name}{" "}
                        <span className="text-[color:var(--color-cyber-muted)]">
                          {f.package_version}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        {f.exploit ? (
                          <PriorityBadge
                            score={f.exploit.priority_score}
                            level={f.exploit.priority_level}
                          />
                        ) : (
                          <span className="text-[10px] text-[color:var(--color-cyber-muted)]">
                            —
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono text-[10px]">
                        {r.accepted_by || "—"}
                      </td>
                      <td className="px-3 py-2 font-mono text-[10px]">
                        {formatDate(r.accepted_at)}
                      </td>
                      <td className="px-3 py-2 font-mono text-[10px]">
                        {r.expires_at ? (
                          <span className={cn(r.expired && "text-red-300")}>
                            {formatDate(r.expires_at)}
                          </span>
                        ) : (
                          <span className="text-[color:var(--color-cyber-muted)]">
                            ∞
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 max-w-[18rem] text-[10px]">
                        {r.reason || (
                          <span className="text-[color:var(--color-cyber-muted)]">
                            (aucune)
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => {
                            if (
                              confirm(
                                `Révoquer l'acceptation de ${f.cve_id} sur ${f.package_name} ? La vuln remontera dans Must-fix.`,
                              )
                            ) {
                              revoke.mutate(f);
                            }
                          }}
                          disabled={revoke.isPending}
                          className="border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-red-500 hover:text-red-300"
                        >
                          Révoquer
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

function RiskAcceptModal({
  finding,
  onClose,
  onSubmit,
  submitting,
  error,
}: {
  finding: Finding;
  onClose: () => void;
  onSubmit: (reason: string, expires_at: string | null) => void;
  submitting: boolean;
  error: string | null;
}) {
  const [reason, setReason] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="cyber-panel max-w-lg w-full p-5 space-y-3 border border-amber-500/40 bg-[color:var(--color-cyber-bg-2)]">
        <div className="flex items-center justify-between">
          <h3 className="cyber-display cyber-glow text-lg text-amber-300">
            Accepter le risque
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
            aria-label="Fermer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="text-[11px] text-[color:var(--color-cyber-muted)]">
          <div>
            <span className="cyber-label text-[9px]">cve</span>{" "}
            <span className="font-mono">{finding.cve_id}</span>
          </div>
          <div>
            <span className="cyber-label text-[9px]">paquet</span>{" "}
            <span className="font-mono">
              {finding.package_name} {finding.package_version}
            </span>
          </div>
          {finding.exploit && (
            <div>
              <span className="cyber-label text-[9px]">priority</span>{" "}
              <PriorityBadge
                score={finding.exploit.priority_score}
                level={finding.exploit.priority_level}
              />
            </div>
          )}
        </div>
        <div>
          <label className="cyber-label mb-1 block text-[10px]">
            Raison (obligatoire)
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="Mitigation en place, feature inutilisée, fix bloqué par compat…"
            className="w-full border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-2 text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
          />
        </div>
        <div>
          <label className="cyber-label mb-1 block text-[10px]">
            Expiration (optionnel — vide = permanent)
          </label>
          <input
            type="date"
            value={expiresAt}
            onChange={(e) => setExpiresAt(e.target.value)}
            className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1 text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
          />
          <div className="mt-1 text-[10px] text-[color:var(--color-cyber-muted)]">
            Une fois expirée, la vuln remonte automatiquement dans la vue
            Must-fix.
          </div>
        </div>
        {error && <div className="text-[10px] text-red-300">{error}</div>}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="flex-1 border border-[color:var(--color-cyber-border)] px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
          >
            Annuler
          </button>
          <button
            type="button"
            disabled={submitting || !reason.trim()}
            onClick={() =>
              onSubmit(
                reason.trim(),
                expiresAt ? new Date(`${expiresAt}T23:59:59Z`).toISOString() : null,
              )
            }
            className={cn(
              "flex-1 border px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] transition",
              "border-amber-500/60 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20",
              "disabled:opacity-50",
            )}
          >
            {submitting ? "..." : "Accepter le risque"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function FindingRow({
  f,
  onToggleAck,
  onOpenRiskModal,
  onRevokeRisk,
}: {
  f: Finding;
  onToggleAck: () => void;
  onOpenRiskModal: (f: Finding) => void;
  onRevokeRisk: (f: Finding) => void;
}) {
  const [open, setOpen] = useState(false);
  const hasAttack =
    !!f.attack_path &&
    (f.attack_path.cwe.length +
      f.attack_path.capec.length +
      f.attack_path.techniques.length +
      f.attack_path.atlas.length >
      0);

  return (
    <>
      <tr
        className={cn(
          "border-b border-[color:var(--color-cyber-border)]/40 transition hover:bg-[color:var(--color-cyber-surface)]/60",
          f.acknowledged && "opacity-50",
        )}
      >
        <td className="px-2 py-2 align-top">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-accent)]"
            aria-label={open ? "Replier" : "Déplier"}
          >
            {open ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </button>
        </td>
        <td className="px-3 py-2 align-top">
          {f.exploit ? (
            <PriorityBadge
              score={f.exploit.priority_score}
              level={f.exploit.priority_level}
            />
          ) : (
            <span className="text-[10px] text-[color:var(--color-cyber-muted)]">—</span>
          )}
        </td>
        <td className="px-3 py-2 align-top">
          <SeverityBadge sev={f.severity} />
        </td>
        <td className="px-3 py-2 align-top font-mono">
          {f.cvss_score != null ? (
            <span
              className={cn(
                f.cvss_score >= 9 && "text-red-300 cyber-glow",
                f.cvss_score >= 7 && f.cvss_score < 9 && "text-orange-300",
                f.cvss_score >= 4 && f.cvss_score < 7 && "text-yellow-200",
                f.cvss_score < 4 && "text-sky-300",
              )}
              title={f.cvss_vector ?? undefined}
            >
              {f.cvss_score.toFixed(1)}
            </span>
          ) : (
            <span className="text-[color:var(--color-cyber-muted)]">—</span>
          )}
        </td>
        <td className="px-3 py-2 align-top">
          <AttackVectorBadge av={f.attack_vector} />
        </td>
        <td className="px-3 py-2 align-top">
          <a
            href={f.url ?? `https://nvd.nist.gov/vuln/detail/${f.cve_id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 font-mono text-[color:var(--color-cyber-accent)] hover:underline"
          >
            {f.cve_id}
            <ExternalLink className="h-3 w-3" />
          </a>
          <div className="mt-0.5 flex flex-wrap items-center gap-1">
            {f.exploit?.kev && (
              <span className="inline-flex items-center gap-1 text-[9px] uppercase tracking-[0.18em] text-red-300">
                <Flame className="h-3 w-3" /> KEV
                {f.exploit.kev.known_ransomware_use && " · ransom"}
              </span>
            )}
            {f.exploit && f.exploit.cert_fr.length > 0 && (
              <CertFrBadge
                alerteCount={
                  f.exploit.cert_fr.filter((b) => b.kind === "alerte").length
                }
                avisCount={
                  f.exploit.cert_fr.filter((b) => b.kind === "avis").length
                }
              />
            )}
          </div>
        </td>
        <td className="px-3 py-2 align-top font-mono">{f.package_name}</td>
        <td className="px-3 py-2 align-top font-mono text-[color:var(--color-cyber-muted)]">
          {f.package_version}
        </td>
        <td className="px-3 py-2 align-top font-mono">
          {f.fixed_in ?? (
            <span className="text-[color:var(--color-cyber-muted)]">—</span>
          )}
        </td>
        <td className="px-3 py-2 align-top">
          {f.exploit ? <MaturityBadge m={f.exploit.exploit_maturity} /> : (
            <span className="text-[10px] text-[color:var(--color-cyber-muted)]">—</span>
          )}
        </td>
        <td className="px-3 py-2 align-top">
          {hasAttack ? (
            <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-accent)]">
              <Target className="h-3 w-3" />
              {(f.attack_path?.techniques.length ?? 0) > 0
                ? `${f.attack_path?.techniques.length} ATT&CK`
                : "CWE/CAPEC"}
            </span>
          ) : (
            <span className="text-[10px] text-[color:var(--color-cyber-muted)]">—</span>
          )}
        </td>
        <td className="px-3 py-2 align-top">
          <div className="flex flex-col gap-1">
            <button
              type="button"
              onClick={onToggleAck}
              className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
            >
              {f.acknowledged ? (
                <>
                  <CheckCircle2 className="h-3 w-3" /> acked
                </>
              ) : (
                <>
                  <EyeOff className="h-3 w-3" /> ack
                </>
              )}
            </button>
            {isRiskAccepted(f) ? (
              <button
                type="button"
                onClick={() => onRevokeRisk(f)}
                className="inline-flex items-center gap-1 border border-amber-500/50 bg-amber-500/10 px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-amber-300 hover:bg-amber-500/20"
                title={`Accepté par ${f.risk_acceptance!.accepted_by}: ${f.risk_acceptance!.reason}`}
              >
                <Scale className="h-3 w-3" /> risk accepté
              </button>
            ) : (
              <button
                type="button"
                onClick={() => onOpenRiskModal(f)}
                className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-amber-500 hover:text-amber-300"
                title="Documenter une décision d'accepter le risque (avec raison)"
              >
                <Scale className="h-3 w-3" /> accepter risk
              </button>
            )}
          </div>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-[color:var(--color-cyber-border)]/40 bg-[color:var(--color-cyber-surface)]/40">
          <td></td>
          <td colSpan={11} className="px-3 py-3">
            <FindingDetails f={f} />
          </td>
        </tr>
      )}
    </>
  );
}

function FindingDetails({ f }: { f: Finding }) {
  return (
    <div className="space-y-4 text-[11px]">
      {f.summary && (
        <div>
          <div className="cyber-label mb-1 text-[10px]">Résumé</div>
          <div className="text-[color:var(--color-cyber-fg)]">{f.summary}</div>
        </div>
      )}
      <ExploitPanel exp={f.exploit} cvss={f.cvss_score} />
      <AttackPathPanel ap={f.attack_path} />
      {f.aliases.length > 0 && (
        <div>
          <div className="cyber-label mb-1 text-[10px]">Aliases</div>
          <div className="flex flex-wrap gap-1">
            {f.aliases.map((a) => (
              <span
                key={a}
                className="border border-[color:var(--color-cyber-border)] px-1.5 py-[1px] font-mono text-[10px] text-[color:var(--color-cyber-muted)]"
              >
                {a}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ExploitPanel({
  exp,
  cvss,
}: {
  exp: ExploitEnrichment | null;
  cvss: number | null;
}) {
  if (!exp) {
    return (
      <div>
        <div className="cyber-label mb-1 text-[10px]">Exploit context</div>
        <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
          Pas encore enrichi — déclenche un scan ou attends le refresh quotidien.
        </div>
      </div>
    );
  }
  const total =
    exp.exploit_db.length + exp.github_pocs.length + exp.metasploit_modules.length;
  return (
    <div className="space-y-2">
      <div className="cyber-label text-[10px]">
        Exploit context (CVSS + EPSS + KEV + maturity)
      </div>
      <div className="flex flex-wrap items-center gap-3 text-[11px]">
        <span className="inline-flex items-center gap-1">
          <span className="cyber-label text-[9px]">priority</span>
          <PriorityBadge score={exp.priority_score} level={exp.priority_level} />
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="cyber-label text-[9px]">maturity</span>
          <MaturityBadge m={exp.exploit_maturity} />
        </span>
        {cvss != null && (
          <span className="font-mono">
            <span className="cyber-label text-[9px]">cvss</span> {cvss.toFixed(1)}
          </span>
        )}
        {exp.epss && (
          <span
            className="font-mono"
            title={`Probabilité d'exploitation 30 j (FIRST EPSS)`}
          >
            <span className="cyber-label text-[9px]">epss</span>{" "}
            {(exp.epss.score * 100).toFixed(1)}% ·{" "}
            <span className="text-[color:var(--color-cyber-muted)]">
              p{(exp.epss.percentile * 100).toFixed(0)}
            </span>
          </span>
        )}
        {exp.last_refreshed_at && (
          <span className="ml-auto text-[9px] text-[color:var(--color-cyber-muted)]">
            enriched {formatDate(exp.last_refreshed_at)}
          </span>
        )}
      </div>

      {exp.kev && (
        <div className="cyber-panel border border-red-500/30 bg-red-500/5 p-2">
          <div className="mb-1 inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.18em] text-red-300">
            <Flame className="h-3 w-3" />
            CISA KEV — activement exploité
            {exp.kev.known_ransomware_use && " · ransomware connu"}
          </div>
          <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
            {exp.kev.vendor && exp.kev.product && (
              <span>
                {exp.kev.vendor} / {exp.kev.product} ·{" "}
              </span>
            )}
            ajouté {formatDate(exp.kev.date_added)}
            {exp.kev.due_date && ` · à patcher avant ${formatDate(exp.kev.due_date)}`}
          </div>
          {exp.kev.short_description && (
            <div className="mt-1 text-[10px]">{exp.kev.short_description}</div>
          )}
          {exp.kev.required_action && (
            <div className="mt-1 text-[10px] text-[color:var(--color-cyber-fg)]">
              <span className="cyber-label text-[9px]">action requise</span>{" "}
              {exp.kev.required_action}
            </div>
          )}
        </div>
      )}

      {exp.cert_fr.length > 0 && (
        <CertFrPanel bulletins={exp.cert_fr} />
      )}

      {total === 0 && !exp.kev && (
        <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
          Pas d'exploit public connu (sources: Exploit-DB, GitHub PoC, Metasploit).
          <br />
          <span className="text-yellow-300/80">
            ⚠️ Absence d'exploit public ≠ absence de risque.
          </span>
        </div>
      )}

      {exp.metasploit_modules.length > 0 && (
        <ExploitList
          label={`Metasploit (${exp.metasploit_modules.length})`}
          icon={<Zap className="h-3 w-3" />}
          accent="orange"
          items={exp.metasploit_modules}
        />
      )}
      {exp.exploit_db.length > 0 && (
        <ExploitList
          label={`Exploit-DB (${exp.exploit_db.length})`}
          icon={<Download className="h-3 w-3" />}
          accent="yellow"
          items={exp.exploit_db.slice(0, 10)}
        />
      )}
      {exp.github_pocs.length > 0 && (
        <ExploitList
          label={`GitHub PoCs (${exp.github_pocs.length})`}
          icon={<Github className="h-3 w-3" />}
          accent="sky"
          items={exp.github_pocs.slice(0, 10)}
        />
      )}
    </div>
  );
}

function CertFrPanel({ bulletins }: { bulletins: CertFrBulletin[] }) {
  // Split alerte vs avis: alerte goes first, more prominently.
  const alertes = bulletins.filter((b) => b.kind === "alerte");
  const avis = bulletins.filter((b) => b.kind === "avis");
  const hasAlerte = alertes.length > 0;
  return (
    <div
      className={cn(
        "cyber-panel p-2",
        hasAlerte
          ? "border border-blue-500/40 bg-blue-500/5"
          : "border border-[color:var(--color-cyber-border)]",
      )}
    >
      <div className="mb-1 inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.18em] text-blue-300">
        🇫🇷 ANSSI / CERT-FR — {alertes.length} alerte(s) · {avis.length} avis
      </div>
      <ul className="space-y-0.5">
        {[...alertes, ...avis].slice(0, 10).map((b) => (
          <li key={b.ref} className="text-[10px]">
            <a
              href={b.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[color:var(--color-cyber-accent)] hover:underline"
            >
              <ExternalLink className="h-3 w-3" />
              <span
                className={cn(
                  "border px-1 py-[1px] font-mono text-[9px] uppercase tracking-[0.15em]",
                  b.kind === "alerte"
                    ? "border-blue-500/60 text-blue-300"
                    : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]",
                )}
              >
                {b.kind}
              </span>
              <span className="font-mono">{b.ref}</span>
              <span className="text-[color:var(--color-cyber-fg)]">— {b.title}</span>
            </a>
            {(b.actively_exploited || b.ransomware_mentioned) && (
              <span className="ml-2 text-[9px] text-red-300">
                {b.actively_exploited && "· activement exploité"}
                {b.ransomware_mentioned && " · ransomware mentionné"}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ExploitList({
  label,
  icon,
  accent,
  items,
}: {
  label: string;
  icon: React.ReactNode;
  accent: "orange" | "yellow" | "sky";
  items: ExploitSourceRef[];
}) {
  const accentCls =
    accent === "orange"
      ? "text-orange-300 border-orange-500/40"
      : accent === "yellow"
      ? "text-yellow-200 border-yellow-500/40"
      : "text-sky-300 border-sky-500/40";
  return (
    <div>
      <div className={cn("mb-1 inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.18em]", accentCls)}>
        {icon}
        {label}
      </div>
      <ul className="space-y-0.5">
        {items.map((e, i) => (
          <li key={`${e.source}-${e.url}-${i}`} className="text-[10px]">
            <a
              href={e.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[color:var(--color-cyber-accent)] hover:underline"
            >
              <ExternalLink className="h-3 w-3" />
              {e.title ?? e.url}
            </a>
            {e.verified && (
              <span className="ml-2 text-emerald-300/80">✓</span>
            )}
            {e.stars != null && e.stars > 0 && (
              <span className="ml-2 text-[color:var(--color-cyber-muted)]">
                ★ {e.stars}
              </span>
            )}
            {e.author && (
              <span className="ml-2 text-[color:var(--color-cyber-muted)]">
                · {e.author}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AttackPathPanel({ ap }: { ap: AttackPath | null }) {
  if (!ap) {
    return (
      <div>
        <div className="cyber-label mb-1 text-[10px]">Chemin d'attaque (CVE2CAPEC)</div>
        <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
          Pas d'entrée dans le dataset (CVE non encore enrichi, ou ID non-CVE).
        </div>
      </div>
    );
  }
  const total =
    ap.cwe.length + ap.capec.length + ap.techniques.length + ap.atlas.length;
  if (total === 0) {
    return (
      <div>
        <div className="cyber-label mb-1 text-[10px]">Chemin d'attaque</div>
        <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
          Entrée vide dans le dataset.
        </div>
      </div>
    );
  }
  return (
    <div>
      <div className="cyber-label mb-1 text-[10px]">
        Chemin d'attaque (CVE → CWE → CAPEC → ATT&amp;CK)
      </div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        <Chips
          label="CWE (faiblesses)"
          items={ap.cwe}
          urlFn={(id) => `https://cwe.mitre.org/data/definitions/${id}.html`}
          prefix="CWE-"
        />
        <Chips
          label="CAPEC (patterns)"
          items={ap.capec}
          urlFn={(id) => `https://capec.mitre.org/data/definitions/${id}.html`}
          prefix="CAPEC-"
        />
        <Chips
          label="MITRE ATT&CK"
          items={ap.techniques}
          urlFn={(id) => {
            // Sub-techniques use a slash on attack.mitre.org: T1574.006 → T1574/006/
            const m = id.match(/^T?(\d+)(?:\.(\d+))?$/);
            if (m) {
              return m[2]
                ? `https://attack.mitre.org/techniques/T${m[1]}/${m[2]}/`
                : `https://attack.mitre.org/techniques/T${m[1]}/`;
            }
            return `https://attack.mitre.org/techniques/${id}/`;
          }}
          prefix=""
          highlight
        />
        <Chips
          label="MITRE ATLAS"
          items={ap.atlas}
          urlFn={(id) => `https://atlas.mitre.org/techniques/${id}`}
          prefix=""
        />
      </div>
    </div>
  );
}

function Chips({
  label,
  items,
  urlFn,
  prefix,
  highlight = false,
}: {
  label: string;
  items: string[];
  urlFn: (id: string) => string;
  prefix: string;
  highlight?: boolean;
}) {
  if (items.length === 0) {
    return (
      <div>
        <div className="cyber-label mb-1 text-[9px]">{label}</div>
        <div className="text-[10px] text-[color:var(--color-cyber-muted)]">—</div>
      </div>
    );
  }
  return (
    <div>
      <div className="cyber-label mb-1 text-[9px]">{label}</div>
      <div className="flex flex-wrap gap-1">
        {items.map((id) => (
          <a
            key={id}
            href={urlFn(id)}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              "border px-1.5 py-[1px] font-mono text-[10px] transition",
              highlight
                ? "border-[color:var(--color-cyber-accent)]/60 bg-[color:var(--color-cyber-accent)]/8 text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/15"
                : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-fg)]",
            )}
          >
            {prefix}
            {id}
          </a>
        ))}
      </div>
    </div>
  );
}

// ---------------------------- MITRE ATT&CK Matrix ---------------------------- #

type MitreTactic = {
  id: string;
  name: string;
  short_name: string;
  url: string;
  description: string;
};
type MitreTechnique = {
  id: string;
  name: string;
  url: string;
  tactics: string[]; // tactic external IDs (TA0001)
  is_sub: boolean;
  parent: string | null;
};

const MITRE = mitreData as {
  tactics: MitreTactic[];
  techniques: MitreTechnique[];
};

function MatrixTab({
  findings,
  cveSubset,
  onClearCveSubset,
  onJumpToVulnsTechnique,
  onJumpToCriticalExploitable,
}: {
  findings: Finding[];
  cveSubset: CveSubset;
  onClearCveSubset: () => void;
  onJumpToVulnsTechnique: (techniqueId: string) => void;
  onJumpToCriticalExploitable: () => void;
}) {
  const [selectedTechnique, setSelectedTechnique] = useState<string | null>(null);
  const [hideUntouched, setHideUntouched] = useState(false);
  const [showSub, setShowSub] = useState(false);

  const criticalExploitableCount = useMemo(
    () => findings.filter(isCriticalExploitable).length,
    [findings],
  );

  const subsetSet = useMemo(
    () => (cveSubset ? new Set(cveSubset.ids) : null),
    [cveSubset],
  );

  // Findings restricted to the active subset (if any) — drives matrix coloring.
  const scopedFindings = useMemo(
    () =>
      subsetSet ? findings.filter((f) => subsetSet.has(f.cve_id)) : findings,
    [findings, subsetSet],
  );

  // Build technique → list of findings using it (via attack_path.techniques).
  const techToFindings = useMemo(() => {
    const m: Record<string, Finding[]> = {};
    for (const f of scopedFindings) {
      const techs = f.attack_path?.techniques ?? [];
      for (const t of techs) {
        (m[t] ||= []).push(f);
      }
    }
    return m;
  }, [scopedFindings]);

  // For each technique, compute max priority_score of its findings (for coloring).
  const techMaxPriority = useMemo(() => {
    const m: Record<string, number> = {};
    for (const [t, fs] of Object.entries(techToFindings)) {
      m[t] = Math.max(...fs.map((f) => f.exploit?.priority_score ?? 0));
    }
    return m;
  }, [techToFindings]);

  // Map tactic short_name → ordered list of techniques in that tactic.
  const tacticTechniques = useMemo(() => {
    const m: Record<string, MitreTechnique[]> = {};
    for (const t of MITRE.techniques) {
      if (!showSub && t.is_sub) continue;
      for (const tacticId of t.tactics) {
        (m[tacticId] ||= []).push(t);
      }
    }
    // Touched-first sort so the relevant cells bubble up
    for (const k of Object.keys(m)) {
      m[k].sort((a, b) => {
        const ta = techToFindings[a.id]?.length ?? 0;
        const tb = techToFindings[b.id]?.length ?? 0;
        if (tb !== ta) return tb - ta;
        return a.id.localeCompare(b.id);
      });
    }
    return m;
  }, [techToFindings, showSub]);

  const touchedCount = useMemo(() => Object.keys(techToFindings).length, [techToFindings]);

  // % de techniques touchées par tactic — pour la barre de couverture.
  const tacticCoverage = useMemo(() => {
    const touchedByTactic: Record<string, Set<string>> = {};
    for (const [techId, fs] of Object.entries(techToFindings)) {
      if (fs.length === 0) continue;
      const tech = MITRE.techniques.find((t) => t.id === techId);
      if (!tech) continue;
      for (const tactic of tech.tactics) {
        (touchedByTactic[tactic] ||= new Set()).add(techId);
      }
    }
    return MITRE.tactics.map((t) => {
      const total = (tacticTechniques[t.id] ?? []).length;
      const touched = (touchedByTactic[t.id]?.size ?? 0);
      return {
        name: t.name,
        coverage: total > 0 ? (touched / total) * 100 : 0,
        touched,
        total,
      };
    });
  }, [techToFindings, tacticTechniques]);

  return (
    <div className="space-y-3">
      <TacticCoverageBar tactics={tacticCoverage} />
      <div className="cyber-panel flex flex-wrap items-center gap-3 p-3 text-[11px]">
        <span className="cyber-label text-[10px]">matrice</span>
        <span>
          <span className="font-mono text-[color:var(--color-cyber-fg)]">
            {touchedCount}
          </span>{" "}
          technique(s) ATT&amp;CK couverte(s) par {scopedFindings.length}
          {subsetSet ? ` / ${findings.length}` : ""} finding(s)
        </span>
        {cveSubset && (
          <span className="inline-flex items-center gap-1 border border-orange-500/60 bg-orange-500/10 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-orange-300">
            <Zap className="h-3 w-3" />
            Sélection : {cveSubset.label} ({cveSubset.ids.length})
            <button
              type="button"
              onClick={onClearCveSubset}
              className="ml-1 hover:text-[color:var(--color-cyber-fg)]"
              aria-label="Retirer la sélection"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        )}
        <button
          type="button"
          onClick={onJumpToCriticalExploitable}
          className="ml-2 inline-flex items-center gap-1 border border-orange-500/60 bg-orange-500/10 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-orange-300 transition hover:bg-orange-500/20"
          title="Saute dans l'onglet Vulnérabilités avec le filtre Critique + exploit (EPSS ≥ 0.7 ou KEV, avec exploit public)"
        >
          <Zap className="h-3 w-3" />
          Critiques exploitables
          <span className="ml-1 rounded-sm bg-orange-500/20 px-1 font-mono">
            {criticalExploitableCount}
          </span>
        </button>
        <label className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          <input
            type="checkbox"
            checked={hideUntouched}
            onChange={(e) => setHideUntouched(e.target.checked)}
            className="accent-[color:var(--color-cyber-accent)]"
          />
          Cacher techniques non touchées
        </label>
        <label className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          <input
            type="checkbox"
            checked={showSub}
            onChange={(e) => setShowSub(e.target.checked)}
            className="accent-[color:var(--color-cyber-accent)]"
          />
          Inclure sub-techniques
        </label>
        <span className="ml-auto text-[10px] text-[color:var(--color-cyber-muted)]">
          Couleur = priority_score max d'une CVE qui exploite cette technique · click = liste filtrée
        </span>
      </div>

      <div className="cyber-panel overflow-x-auto p-2">
        <div
          className="grid gap-2"
          style={{
            gridTemplateColumns: `repeat(${MITRE.tactics.length}, minmax(180px, 1fr))`,
          }}
        >
          {MITRE.tactics.map((tactic) => (
            <div key={tactic.id} className="flex flex-col gap-1">
              <a
                href={tactic.url}
                target="_blank"
                rel="noopener noreferrer"
                className="cyber-label text-[10px] hover:text-[color:var(--color-cyber-accent)]"
                title={tactic.description}
              >
                {tactic.name}
              </a>
              <div className="cyber-hatch h-px w-full" />
              <div className="flex flex-col gap-1">
                {(tacticTechniques[tactic.id] ?? [])
                  .filter((t) => !hideUntouched || techToFindings[t.id])
                  .map((t) => {
                    const findingsHere = techToFindings[t.id] ?? [];
                    const count = findingsHere.length;
                    const maxPri = techMaxPriority[t.id] ?? 0;
                    return (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => setSelectedTechnique(t.id)}
                        className={cn(
                          "group flex items-center justify-between gap-1 border px-1.5 py-1 text-left text-[10px] transition",
                          count === 0 &&
                            "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]/50 hover:text-[color:var(--color-cyber-fg)]",
                          count > 0 &&
                            maxPri < 40 &&
                            "border-sky-500/40 bg-sky-500/5 text-sky-300",
                          count > 0 &&
                            maxPri >= 40 &&
                            maxPri < 60 &&
                            "border-yellow-500/50 bg-yellow-500/10 text-yellow-200",
                          count > 0 &&
                            maxPri >= 60 &&
                            maxPri < 80 &&
                            "border-orange-500/60 bg-orange-500/10 text-orange-300",
                          count > 0 &&
                            maxPri >= 80 &&
                            "border-red-500/70 bg-red-500/15 text-red-300 cyber-glow",
                          selectedTechnique === t.id &&
                            "ring-1 ring-[color:var(--color-cyber-accent)]",
                        )}
                        title={`${t.id} — ${t.name}\n${count} CVE`}
                      >
                        <span className={cn("truncate", t.is_sub && "ml-2")}>
                          {t.id} {t.name}
                        </span>
                        {count > 0 && (
                          <span className="ml-1 shrink-0 font-mono">×{count}</span>
                        )}
                      </button>
                    );
                  })}
              </div>
            </div>
          ))}
        </div>
      </div>

      {selectedTechnique && (
        <TechniqueDetail
          techniqueId={selectedTechnique}
          findings={techToFindings[selectedTechnique] ?? []}
          onClose={() => setSelectedTechnique(null)}
          onJumpToVulns={() => onJumpToVulnsTechnique(selectedTechnique)}
        />
      )}
    </div>
  );
}

function TechniqueDetail({
  techniqueId,
  findings,
  onClose,
  onJumpToVulns,
}: {
  techniqueId: string;
  findings: Finding[];
  onClose: () => void;
  onJumpToVulns: () => void;
}) {
  const tech = MITRE.techniques.find((t) => t.id === techniqueId);
  if (!tech) return null;
  const sorted = [...findings].sort(
    (a, b) => (b.exploit?.priority_score ?? 0) - (a.exploit?.priority_score ?? 0),
  );
  return (
    <div className="cyber-panel border border-[color:var(--color-cyber-accent)]/40 p-4">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <div className="cyber-display cyber-glow text-base">
            {tech.id} — {tech.name}
          </div>
          <a
            href={tech.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[10px] text-[color:var(--color-cyber-accent)] hover:underline"
          >
            <ExternalLink className="h-3 w-3" />
            attack.mitre.org
          </a>
          {tech.is_sub && tech.parent && (
            <span className="ml-3 text-[10px] text-[color:var(--color-cyber-muted)]">
              sous-technique de {tech.parent}
            </span>
          )}
        </div>
        <div className="flex items-start gap-2">
          <button
            type="button"
            onClick={onJumpToVulns}
            className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20"
            title="Ouvre la liste filtrée sur cette technique dans l'onglet Vulnérabilités"
          >
            Voir dans Vulnérabilités →
          </button>
          <button
            type="button"
            onClick={onClose}
            className="text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
      <div className="text-[11px] text-[color:var(--color-cyber-muted)]">
        {sorted.length} CVE qui exploite(nt) cette technique sur ce Slate :
      </div>
      <ul className="mt-2 space-y-1">
        {sorted.slice(0, 50).map((f) => (
          <li
            key={`${f.cve_id}|${f.package_name}`}
            className="flex items-center gap-2 text-[10px]"
          >
            {f.exploit && (
              <PriorityBadge
                score={f.exploit.priority_score}
                level={f.exploit.priority_level}
              />
            )}
            <SeverityBadge sev={f.severity} />
            <AttackVectorBadge av={f.attack_vector} />
            <a
              href={f.url ?? `https://nvd.nist.gov/vuln/detail/${f.cve_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono text-[color:var(--color-cyber-accent)] hover:underline"
            >
              {f.cve_id}
            </a>
            <span className="font-mono text-[color:var(--color-cyber-muted)]">
              {f.package_name}
            </span>
            {f.exploit?.kev && (
              <span className="text-red-300">
                <Flame className="inline h-3 w-3" /> KEV
              </span>
            )}
          </li>
        ))}
        {sorted.length > 50 && (
          <li className="text-[10px] text-[color:var(--color-cyber-muted)]">
            … {sorted.length - 50} de plus
          </li>
        )}
      </ul>
    </div>
  );
}

function InventoryTab({ snapshotId }: { snapshotId: number | null }) {
  const [search, setSearch] = useState("");
  const [hideVendor, setHideVendor] = useState(false);

  const snapQ = useQuery({
    queryKey: ["security", "snapshot", snapshotId],
    queryFn: () => (snapshotId == null ? Promise.resolve(null) : getSnapshot(snapshotId)),
    enabled: snapshotId != null,
  });

  // Findings query for the chart (top packages by CVE count). We piggyback
  // on the parent's findings cache via React Query rather than re-fetching.
  const findingsQ = useQuery({
    queryKey: ["security", "findings"],
    queryFn: () => getFindings(),
    staleTime: 60_000,
  });

  if (snapshotId == null) {
    return (
      <div className="cyber-panel p-6 text-center text-sm text-[color:var(--color-cyber-muted)]">
        Aucun snapshot disponible.
      </div>
    );
  }
  if (snapQ.isLoading) {
    return (
      <div className="cyber-panel p-4 text-xs text-[color:var(--color-cyber-muted)]">
        Chargement de l'inventaire…
      </div>
    );
  }
  const snap = snapQ.data as SnapshotDetail | null;
  if (!snap) {
    return (
      <div className="cyber-panel p-4 text-xs text-red-300">
        Erreur de chargement.
      </div>
    );
  }

  const q = search.toLowerCase().trim();
  const pkgs = snap.packages
    .filter((p) => !hideVendor || !p.vendor_specific)
    .filter(
      (p) => !q || p.name.toLowerCase().includes(q) || p.version.toLowerCase().includes(q),
    );

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <Info label="Target" value={snap.openwrt_target || "—"} />
        <Info label="Arch" value={snap.openwrt_arch || "—"} />
        <Info label="Board" value={snap.board_name || "—"} />
        <Info label="Hostname" value={snap.hostname || "—"} />
      </div>
      {findingsQ.data && (
        <TopPackagesBar findings={findingsQ.data.findings} top={10} />
      )}
      {snap.openwrt_taints && (
        <div className="text-[10px] text-yellow-300">
          taints : {snap.openwrt_taints}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1">
          <Search className="h-3.5 w-3.5 text-[color:var(--color-cyber-muted)]" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="paquet, version…"
            className="bg-transparent text-xs text-[color:var(--color-cyber-fg)] outline-none placeholder:text-[color:var(--color-cyber-muted)]"
          />
        </div>
        <label className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          <input
            type="checkbox"
            checked={hideVendor}
            onChange={(e) => setHideVendor(e.target.checked)}
            className="accent-[color:var(--color-cyber-accent)]"
          />
          Cacher vendor (gl-*)
        </label>
        <span className="ml-auto text-[10px] text-[color:var(--color-cyber-muted)]">
          {pkgs.length} / {snap.packages.length}
        </span>
      </div>
      <div className="cyber-panel max-h-[60vh] overflow-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 border-b border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]">
            <tr>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Paquet</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Version</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Upstream</th>
              <th className="cyber-label px-3 py-2 text-left text-[10px]">Type</th>
            </tr>
          </thead>
          <tbody>
            {pkgs.map((p) => (
              <tr
                key={p.name}
                className="border-b border-[color:var(--color-cyber-border)]/40"
              >
                <td className="px-3 py-1.5 font-mono">{p.name}</td>
                <td className="px-3 py-1.5 font-mono">{p.version}</td>
                <td className="px-3 py-1.5 font-mono text-[color:var(--color-cyber-muted)]">
                  {p.upstream_version}
                </td>
                <td className="px-3 py-1.5">
                  {p.vendor_specific ? (
                    <span className="border border-yellow-500/40 px-1.5 py-[1px] text-[9px] uppercase tracking-[0.18em] text-yellow-300">
                      vendor
                    </span>
                  ) : (
                    <span className="border border-[color:var(--color-cyber-border)] px-1.5 py-[1px] text-[9px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
                      upstream
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SnapshotsTab({
  snapshots,
  loading,
}: {
  snapshots: import("@/types/security").SnapshotSummary[];
  loading: boolean;
}) {
  const historyQ = useQuery({
    queryKey: ["security", "risk-score-history"],
    queryFn: () => getRiskScoreHistory(30),
    staleTime: 60_000,
  });

  if (loading) {
    return (
      <div className="cyber-panel p-4 text-xs text-[color:var(--color-cyber-muted)]">
        Chargement…
      </div>
    );
  }
  if (snapshots.length === 0) {
    return (
      <div className="cyber-panel p-4 text-xs text-[color:var(--color-cyber-muted)]">
        Aucun snapshot.
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {historyQ.data && historyQ.data.length > 1 && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <RiskScoreTrend points={historyQ.data} />
          <KevWeaponizedTrend points={historyQ.data} />
        </div>
      )}
      <div className="cyber-panel overflow-hidden">
      <table className="w-full text-xs">
        <thead className="border-b border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]">
          <tr>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Date</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">OpenWrt</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Firmware</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Kernel</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Paquets</th>
            <th className="cyber-label px-3 py-2 text-left text-[10px]">Status</th>
          </tr>
        </thead>
        <tbody>
          {snapshots.map((s) => (
            <tr
              key={s.id}
              className="border-b border-[color:var(--color-cyber-border)]/40"
            >
              <td className="px-3 py-2 font-mono">{formatDate(s.taken_at)}</td>
              <td className="px-3 py-2 font-mono">{s.openwrt_release || "—"}</td>
              <td className="px-3 py-2 font-mono">{s.firmware_version || "—"}</td>
              <td className="px-3 py-2 font-mono">{s.kernel || "—"}</td>
              <td className="px-3 py-2 font-mono">{s.package_count}</td>
              <td className="px-3 py-2">
                <ScanStatusBadge status={s.scan_status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}
