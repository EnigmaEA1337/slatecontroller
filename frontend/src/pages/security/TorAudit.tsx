import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Info as InfoIcon,
  Loader2,
  RefreshCw,
  ShieldAlert,
  ShieldOff,
  Wrench,
  XCircle,
} from "lucide-react";

import { api } from "@/api/client";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";

type Severity = "critical" | "high" | "medium" | "low" | "info" | "pass";
type CheckStatus = "pass" | "fail" | "warn" | "info" | "skip";

interface Finding {
  id: string;
  label: string;
  status: CheckStatus;
  severity: Severity;
  evidence: string;
  remediation: string;
}

interface TorAuditReport {
  score: number;
  tor_installed: boolean;
  tor_running: boolean;
  transparent_networks: string[];
  generated_at: string;
  findings: Finding[];
}

async function fetchTorAudit(): Promise<TorAuditReport> {
  const { data } = await api.get<TorAuditReport>("/api/tor/audit");
  return data;
}

async function applyTor(): Promise<{ ok: boolean; output: string }> {
  // POST /api/tor/apply : re-sync the active profile JSON + run only the
  // tor handler on the device (slate-ctrl apply-only tor). Takes 1-3 s,
  // returns the handler's stdout.
  const { data } = await api.post<{ ok: boolean; output: string }>(
    "/api/tor/apply", undefined, { timeout: 30_000 },
  );
  return data;
}

// Mapping finding-id → fix-action label. Currently every fail/warn that
// has a remediation is fixed by re-running the tor handler (which sets
// torrc + iptables + ip6tables + flushes conntrack idempotently). The
// label just sharpens the operator's mental model of WHAT will happen.
function fixLabelFor(id: string): string {
  if (id.startsWith("tor.port.")) return "Bind sur la gateway";
  if (id === "tor.ipv6.forward_drop") return "Poser le DROP ip6tables";
  if (id === "tor.kill_switch") return "Installer le kill-switch";
  if (id === "tor.conntrack.orphans") return "Flush conntrack";
  return "Corriger";
}

// Aligned with TailscaleAuditPanel's SEV_STYLE for a uniform look.
const SEV_STYLE: Record<Severity, { chip: string; label: string }> = {
  critical: { chip: "border-red-500/60 bg-red-500/10 text-red-300", label: "CRITICAL" },
  high:     { chip: "border-orange-500/60 bg-orange-500/10 text-orange-300", label: "HIGH" },
  medium:   { chip: "border-yellow-500/60 bg-yellow-500/10 text-yellow-200", label: "MEDIUM" },
  low:      { chip: "border-sky-500/60 bg-sky-500/10 text-sky-200", label: "LOW" },
  info:     { chip: "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]", label: "INFO" },
  pass:     { chip: "border-emerald-500/60 bg-emerald-500/10 text-emerald-300 cyber-glow-ok", label: "OK" },
};

const SEV_ORDER: Severity[] = ["critical", "high", "medium", "low", "info", "pass"];

function statusIcon(status: CheckStatus, severity: Severity) {
  if (status === "pass") return <CheckCircle2 className="h-4 w-4 text-emerald-400" />;
  if (status === "fail" || status === "warn") {
    if (severity === "critical" || severity === "high") {
      return <XCircle className="h-4 w-4 text-red-400" />;
    }
    return <AlertTriangle className="h-4 w-4 text-yellow-300" />;
  }
  if (status === "skip") return <ShieldOff className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />;
  return <InfoIcon className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />;
}

function scoreColor(score: number): string {
  if (score >= 90) return "text-emerald-300 cyber-glow-ok";
  if (score >= 75) return "text-sky-300";
  if (score >= 60) return "text-yellow-200";
  if (score >= 40) return "text-orange-300";
  return "text-red-300";
}

function gradeFor(score: number): string {
  if (score >= 90) return "A";
  if (score >= 75) return "B";
  if (score >= 60) return "C";
  if (score >= 40) return "D";
  return "F";
}

function gradeColor(grade: string): string {
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

/**
 * Tor gateway security audit page. Same visual vocabulary as
 * /security/tailscale (Tailscale Audit) so the Sécurité group reads as
 * one coherent piece :
 *   - cyber-display headline + cyber-glow
 *   - cyber-display 5xl score + grade-card + 3 CounterBox (pass/warn/fail)
 *   - findings list with chevron + statusIcon + severity chip,
 *     collapsible — fail/warn auto-expanded.
 */
export default function SecurityTorAudit() {
  const t = useT();
  const qc = useQueryClient();
  const auditQ = useQuery({
    queryKey: ["security", "tor-audit"],
    queryFn: fetchTorAudit,
    refetchOnWindowFocus: false,
    staleTime: 5 * 60 * 1000,
  });
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  function toggle(id: string) {
    setOpenIds((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Per-finding "Corriger" : we track WHICH finding triggered the fix so
  // the spinner appears on the right card, not all of them.
  const [activeFixId, setActiveFixId] = useState<string | null>(null);
  const fixMut = useMutation({
    mutationFn: applyTor,
    onSettled: () => {
      setActiveFixId(null);
      // Refresh audit + status to reflect post-fix state.
      qc.invalidateQueries({ queryKey: ["security", "tor-audit"] });
      qc.invalidateQueries({ queryKey: ["tor", "status"] });
    },
  });
  function applyFix(findingId: string) {
    setActiveFixId(findingId);
    fixMut.mutate();
  }

  const sortedFindings = useMemo(() => {
    const f = auditQ.data?.findings ?? [];
    return [...f].sort(
      (a, b) =>
        SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity),
    );
  }, [auditQ.data]);

  const report = auditQ.data;
  const counts = useMemo(() => {
    const f = auditQ.data?.findings ?? [];
    return {
      pass: f.filter((x) => x.status === "pass").length,
      warn: f.filter((x) => x.status === "warn").length,
      fail: f.filter((x) => x.status === "fail").length,
    };
  }, [auditQ.data]);

  return (
    <div className="space-y-6 p-6">
      {/* Page heading — mirrors SecurityHardening / TailscaleAudit. */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <ShieldAlert className="cyber-glow h-5 w-5" />
          <h1 className="cyber-display cyber-glow text-2xl">
            {t("tor.title_audit").toUpperCase()}
          </h1>
        </div>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          {t("tor.audit_subtitle")}
        </p>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-2 text-xs text-[color:var(--color-cyber-muted)]">
        <span className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-[2px] text-[10px] uppercase tracking-[0.18em]">
          local probes
        </span>
        {report?.generated_at && (
          <span className="text-[10px] uppercase tracking-[0.18em]">
            scan {new Date(report.generated_at).toLocaleTimeString("fr-FR")}
          </span>
        )}
        <button
          type="button"
          onClick={() => auditQ.refetch()}
          disabled={auditQ.isFetching}
          className="ml-auto inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-50"
        >
          <RefreshCw
            className={cn("h-3 w-3", auditQ.isFetching && "animate-spin")}
          />
          {auditQ.isFetching ? "scan…" : "relancer"}
        </button>
      </div>

      {auditQ.isLoading && (
        <p className="cyber-label cyber-cursor">scan en cours…</p>
      )}

      {report && (
        <>
          {/* Scorecard — same layout as TailscaleAuditPanel. */}
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
                  gradeColor(gradeFor(report.score)),
                )}
                style={{ fontSize: "2.5rem", lineHeight: 1 }}
              >
                {gradeFor(report.score)}
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3 text-xs">
              <CounterBox
                label="Pass" value={counts.pass}
                className="border-emerald-500/40 text-emerald-300"
              />
              <CounterBox
                label="Warn" value={counts.warn}
                className="border-yellow-500/40 text-yellow-200"
              />
              <CounterBox
                label="Fail" value={counts.fail}
                className="border-red-500/40 text-red-300"
              />
              <div className="col-span-3 grid grid-cols-2 gap-2 text-[10px] text-[color:var(--color-cyber-muted)]">
                <div>
                  <span className="cyber-label">tor installé</span>{" "}
                  <span className="font-mono">{report.tor_installed ? "oui" : "non"}</span>
                </div>
                <div>
                  <span className="cyber-label">daemon</span>{" "}
                  <span className="font-mono">{report.tor_running ? "running" : "stopped"}</span>
                </div>
                <div>
                  <span className="cyber-label">transparent nets</span>{" "}
                  <span className="font-mono">{report.transparent_networks.join(" · ") || "—"}</span>
                </div>
                <div>
                  <span className="cyber-label">total checks</span>{" "}
                  <span className="font-mono">{report.findings.length}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Findings list (collapsible). */}
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
                      {f.remediation && (
                        <div className="border border-[color:var(--color-cyber-accent)]/40 bg-[color:var(--color-cyber-accent)]/5 p-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="cyber-label text-[color:var(--color-cyber-accent)]">
                              remediation
                            </span>
                            {(f.status === "fail" || f.status === "warn") && (
                              <button
                                type="button"
                                onClick={() => applyFix(f.id)}
                                disabled={fixMut.isPending}
                                className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-2 py-1 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-50"
                              >
                                {fixMut.isPending && activeFixId === f.id ? (
                                  <>
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                    fix…
                                  </>
                                ) : (
                                  <>
                                    <Wrench className="h-3 w-3" />
                                    {fixLabelFor(f.id)}
                                  </>
                                )}
                              </button>
                            )}
                          </div>
                          <div className="mt-1 text-[color:var(--color-cyber-fg)]">
                            {f.remediation}
                          </div>
                          {fixMut.isError && activeFixId === f.id && (
                            <div className="mt-2 text-[10px] text-red-300">
                              <AlertTriangle className="mr-1 inline h-3 w-3" />
                              {errorMessage(fixMut.error)}
                            </div>
                          )}
                          {fixMut.isSuccess && activeFixId === null && fixMut.data?.ok && f.status !== "pass" && (
                            <div className="mt-2 text-[10px] text-emerald-300">
                              <CheckCircle2 className="mr-1 inline h-3 w-3" />
                              Fix appliqué — relance du scan en cours…
                            </div>
                          )}
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
