import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Cloud,
  CloudOff,
  ExternalLink,
  Info as InfoIcon,
  KeyRound,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  auditTailscale,
  deleteAdminPat,
  getAdminPatStatus,
  setAdminPat,
} from "@/api/tailscale";
import type {
  AuditCheckStatus,
  AuditFinding,
  AuditSeverity,
} from "@/types/tailscale";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";


const SEV_STYLE: Record<AuditSeverity, { chip: string; label: string }> = {
  critical: { chip: "border-red-500/60 bg-red-500/10 text-red-300", label: "CRITICAL" },
  high:     { chip: "border-orange-500/60 bg-orange-500/10 text-orange-300", label: "HIGH" },
  medium:   { chip: "border-yellow-500/60 bg-yellow-500/10 text-yellow-200", label: "MEDIUM" },
  low:      { chip: "border-sky-500/60 bg-sky-500/10 text-sky-200", label: "LOW" },
  info:     { chip: "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]", label: "INFO" },
  pass:     { chip: "border-emerald-500/60 bg-emerald-500/10 text-emerald-300 cyber-glow-ok", label: "OK" },
};

const SEV_ORDER: AuditSeverity[] = ["critical", "high", "medium", "low", "info", "pass"];

function statusIcon(status: AuditCheckStatus, severity: AuditSeverity) {
  if (status === "pass") {
    return <CheckCircle2 className="h-4 w-4 text-emerald-400" />;
  }
  if (status === "fail" || status === "warn") {
    if (severity === "critical" || severity === "high") {
      return <XCircle className="h-4 w-4 text-red-400" />;
    }
    return <AlertTriangle className="h-4 w-4 text-yellow-300" />;
  }
  if (status === "skip") {
    return <ShieldOff className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />;
  }
  return <InfoIcon className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />;
}

export function scoreColor(score: number): string {
  if (score >= 90) return "text-emerald-300 cyber-glow-ok";
  if (score >= 75) return "text-sky-300";
  if (score >= 60) return "text-yellow-200";
  if (score >= 40) return "text-orange-300";
  return "text-red-300";
}

export function gradeColor(grade: string): string {
  if (grade === "A") return "border-emerald-500/60 text-emerald-300 cyber-glow-ok";
  if (grade === "B") return "border-sky-500/60 text-sky-300";
  if (grade === "C") return "border-yellow-500/60 text-yellow-200";
  if (grade === "D") return "border-orange-500/60 text-orange-300";
  return "border-red-500/60 text-red-300";
}

function CounterBox({
  label, value, className,
}: { label: string; value: number; className?: string }) {
  return (
    <div className={cn("border p-3 text-center", className)}>
      <div className="cyber-display text-2xl leading-none">{value}</div>
      <div className="mt-1 text-[9px] uppercase tracking-[0.18em]">{label}</div>
    </div>
  );
}

export default function TailscaleAuditPanel({
  daemonRunning,
}: { daemonRunning: boolean }) {
  const qc = useQueryClient();
  const auditQ = useQuery({
    queryKey: ["tailscale", "audit"],
    queryFn: auditTailscale,
    enabled: daemonRunning,
    // The audit is heavy (6 parallel SSH probes ~5-10s) — don't refetch on
    // window focus or every minute. User-triggered refresh only.
    refetchOnWindowFocus: false,
    staleTime: 5 * 60 * 1000,
  });
  const patQ = useQuery({
    queryKey: ["tailscale", "admin_pat"],
    queryFn: getAdminPatStatus,
    refetchOnWindowFocus: false,
  });
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  const [patInput, setPatInput] = useState("");
  const [patFormOpen, setPatFormOpen] = useState(false);

  const savePatMutation = useMutation({
    mutationFn: ({ pat }: { pat: string }) => setAdminPat(pat),
    onSuccess: () => {
      setPatInput("");
      setPatFormOpen(false);
      qc.invalidateQueries({ queryKey: ["tailscale", "admin_pat"] });
      qc.invalidateQueries({ queryKey: ["tailscale", "audit"] });
    },
  });
  const deletePatMutation = useMutation({
    mutationFn: deleteAdminPat,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tailscale", "admin_pat"] });
      qc.invalidateQueries({ queryKey: ["tailscale", "audit"] });
    },
  });

  function toggle(id: string) {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const report = auditQ.data;
  const sortedFindings: AuditFinding[] = report
    ? [...report.findings].sort((a, b) => {
        const sa = SEV_ORDER.indexOf(a.severity);
        const sb = SEV_ORDER.indexOf(b.severity);
        if (sa !== sb) return sa - sb;
        const rank: Record<AuditCheckStatus, number> = {
          fail: 0, warn: 1, skip: 2, info: 3, pass: 4,
        };
        return rank[a.status] - rank[b.status];
      })
    : [];

  return (
    <div className="cyber-panel space-y-4 p-5">
      <div className="flex flex-wrap items-center gap-3">
        <ShieldCheck className="cyber-glow h-5 w-5" />
        <h2 className="cyber-display cyber-glow text-base">Audit sécurité Tailscale</h2>
        {patQ.data?.configured ? (
          <span className="inline-flex items-center gap-1 border border-emerald-500/60 bg-emerald-500/10 px-2 py-[2px] text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-300 cyber-glow-ok">
            <Cloud className="h-3 w-3" />
            local + cloud
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-[2px] text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
            <CloudOff className="h-3 w-3" />
            local only
          </span>
        )}
        <button
          type="button"
          onClick={() => auditQ.refetch()}
          disabled={!daemonRunning || auditQ.isFetching}
          className="ml-auto inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-50"
        >
          <RefreshCw className={cn("h-3 w-3", auditQ.isFetching && "animate-spin")} />
          {auditQ.isFetching ? "scan…" : "relancer"}
        </button>
      </div>

      {/* PAT admin config */}
      <div className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]">
        <button
          type="button"
          onClick={() => setPatFormOpen((v) => !v)}
          className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-[color:var(--color-cyber-surface-elev)]"
        >
          {patFormOpen ? (
            <ChevronDown className="h-3 w-3 text-[color:var(--color-cyber-muted)]" />
          ) : (
            <ChevronRight className="h-3 w-3 text-[color:var(--color-cyber-muted)]" />
          )}
          <KeyRound className="h-3 w-3 text-[color:var(--color-cyber-accent)]" />
          <span className="flex-1 text-xs text-[color:var(--color-cyber-fg)]">
            {patQ.data?.configured
              ? `PAT admin configuré — étend l'audit avec les checks tailnet`
              : "Étendre l'audit avec un PAT admin Tailscale"}
          </span>
          {patQ.data?.configured && patQ.data.tailnet && (
            <span className="font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
              {patQ.data.tailnet}
            </span>
          )}
        </button>
        {patFormOpen && (
          <div className="space-y-3 border-t border-[color:var(--color-cyber-border)] px-3 py-3 text-[11px]">
            <p className="text-[color:var(--color-cyber-muted)]">
              Génère un Personal Access Token dans{" "}
              <a
                href="https://login.tailscale.com/admin/settings/keys"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-[color:var(--color-cyber-accent)] hover:underline"
              >
                admin.tailscale.com → Settings → Keys
                <ExternalLink className="h-3 w-3" />
              </a>{" "}
              (onglet <em>API access tokens</em> — PAS celui des Auth keys ! Expire 90j max, scopes read-only suffisent).
              Stocké chiffré en DB, jamais renvoyé en clair, validé contre l'API avant
              sauvegarde.
            </p>
            {patQ.data?.configured ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1 border border-emerald-500/60 bg-emerald-500/10 px-2 py-1 text-[10px] text-emerald-300">
                  <CheckCircle2 className="h-3 w-3" />
                  PAT actif {patQ.data.last_verified_at && (
                    <>(vérifié {new Date(patQ.data.last_verified_at).toLocaleString("fr-FR")})</>
                  )}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    if (confirm("Supprimer le PAT stocké ?")) {
                      deletePatMutation.mutate();
                    }
                  }}
                  disabled={deletePatMutation.isPending}
                  className="inline-flex items-center gap-1 border border-red-500/60 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-red-300 hover:bg-red-500/10 disabled:opacity-50"
                >
                  <Trash2 className="h-3 w-3" />
                  Supprimer
                </button>
              </div>
            ) : (
              <>
                <input
                  type="password"
                  value={patInput}
                  onChange={(e) => setPatInput(e.target.value)}
                  placeholder="tskey-api-..."
                  className="w-full border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
                  autoComplete="off"
                />
                <button
                  type="button"
                  onClick={() => savePatMutation.mutate({ pat: patInput })}
                  disabled={!patInput.trim() || savePatMutation.isPending}
                  className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-50"
                >
                  {savePatMutation.isPending ? "Validation…" : "Valider & stocker"}
                </button>
                {savePatMutation.isError && (
                  <div className="border border-red-500/40 bg-red-500/5 p-2 text-[10px] text-red-300">
                    <AlertTriangle className="mr-1 inline h-3 w-3" />
                    {errorMessage(savePatMutation.error)}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {!daemonRunning && (
        <div className="text-[11px] text-[color:var(--color-cyber-muted)]">
          Daemon Tailscale arrêté — audit indisponible. Connecte d'abord (page Tailscale).
        </div>
      )}

      {daemonRunning && auditQ.isLoading && (
        <div className="text-[11px] text-[color:var(--color-cyber-muted)]">
          Probe en cours via SSH (~5-10s)…
        </div>
      )}

      {daemonRunning && auditQ.isError && (
        <div className="border border-red-500/40 bg-red-500/5 p-2 text-[11px] text-red-300">
          <AlertTriangle className="mr-1 inline h-3 w-3" />
          {errorMessage(auditQ.error)}
        </div>
      )}

      {report && (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[auto_1fr]">
            <div className="flex items-center gap-4 border border-[color:var(--color-cyber-border)] p-4">
              <div className="text-center">
                <div className={cn("cyber-display text-5xl leading-none", scoreColor(report.score))}>
                  {report.score}
                </div>
                <div className="mt-1 text-[9px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
                  / 100
                </div>
              </div>
              <div
                className={cn(
                  "flex h-16 w-16 items-center justify-center border-2 font-bold",
                  gradeColor(report.grade),
                )}
                style={{ fontSize: "2.5rem", lineHeight: 1 }}
              >
                {report.grade}
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3 text-xs">
              <CounterBox
                label="Pass" value={report.pass_count}
                className="border-emerald-500/40 text-emerald-300"
              />
              <CounterBox
                label="Warn" value={report.warn_count}
                className="border-yellow-500/40 text-yellow-200"
              />
              <CounterBox
                label="Fail" value={report.fail_count}
                className="border-red-500/40 text-red-300"
              />
              <div className="col-span-3 grid grid-cols-2 gap-2 text-[10px] text-[color:var(--color-cyber-muted)]">
                <div>
                  <span className="cyber-label">version</span>{" "}
                  <span className="font-mono">{report.raw_summary.version.split("\n")[0]}</span>
                </div>
                <div>
                  <span className="cyber-label">tailnet</span>{" "}
                  <span className="font-mono">{report.raw_summary.tailnet}</span>
                </div>
                <div>
                  <span className="cyber-label">peers</span>{" "}
                  <span className="font-mono">{report.raw_summary.peers}</span>
                </div>
                <div>
                  <span className="cyber-label">self ip</span>{" "}
                  <span className="font-mono">{report.raw_summary.self_ip[0] ?? "—"}</span>
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-1.5">
            {sortedFindings.map((f) => {
              const isOpen =
                openIds.has(f.id) ||
                f.status === "fail" ||
                f.status === "warn";
              const sev = SEV_STYLE[f.severity];
              return (
                <div
                  key={f.id}
                  className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]"
                >
                  <button
                    type="button"
                    onClick={() => toggle(f.id)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-[color:var(--color-cyber-surface-elev)]"
                  >
                    {isOpen ? (
                      <ChevronDown className="h-3 w-3 text-[color:var(--color-cyber-muted)]" />
                    ) : (
                      <ChevronRight className="h-3 w-3 text-[color:var(--color-cyber-muted)]" />
                    )}
                    {statusIcon(f.status, f.severity)}
                    <span className="flex-1 text-xs text-[color:var(--color-cyber-fg)]">
                      {f.label}
                    </span>
                    <span
                      className={cn(
                        "border px-1.5 py-[1px] text-[9px] font-bold uppercase tracking-[0.18em]",
                        sev.chip,
                      )}
                    >
                      {sev.label}
                    </span>
                  </button>
                  {isOpen && (
                    <div className="space-y-1.5 border-t border-[color:var(--color-cyber-border)] px-3 py-2 text-[11px]">
                      <div>
                        <span className="cyber-label">evidence</span>{" "}
                        <span className="font-mono text-[color:var(--color-cyber-fg)]">
                          {f.evidence}
                        </span>
                      </div>
                      {f.recommendation && (
                        <div className="border border-[color:var(--color-cyber-accent)]/40 bg-[color:var(--color-cyber-accent)]/5 p-2">
                          <span className="cyber-label text-[color:var(--color-cyber-accent)]">
                            recommendation
                          </span>
                          <div className="mt-1 text-[color:var(--color-cyber-fg)]">
                            {f.recommendation}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
