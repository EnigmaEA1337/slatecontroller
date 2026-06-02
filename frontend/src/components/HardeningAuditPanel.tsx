import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  Info as InfoIcon,
  Loader2,
  RefreshCw,
  ShieldOff,
  Wrench,
  XCircle,
} from "lucide-react";

import { api } from "@/api/client";
import { getSlateHardening } from "@/api/slate";
import type { HardeningCheck } from "@/types/hardening";
import { errorMessage } from "@/lib/error-utils";
import { cn } from "@/lib/utils";

async function applyHardeningFix(checkName: string): Promise<{
  ok: boolean;
  status: string;
  message: string;
  evidence: string;
}> {
  const { data } = await api.post(
    "/api/slate/hardening/fix",
    null,
    { params: { check_name: checkName }, timeout: 60_000 },
  );
  return data;
}

type Status = "pass" | "warn" | "fail" | "info" | "skip";
type Severity = "critical" | "high" | "medium" | "low" | "info" | "pass";

const SEV_STYLE: Record<Severity, { chip: string; label: string }> = {
  critical: { chip: "border-red-500/60 bg-red-500/10 text-red-300", label: "CRITICAL" },
  high:     { chip: "border-orange-500/60 bg-orange-500/10 text-orange-300", label: "HIGH" },
  medium:   { chip: "border-yellow-500/60 bg-yellow-500/10 text-yellow-200", label: "MEDIUM" },
  low:      { chip: "border-sky-500/60 bg-sky-500/10 text-sky-200", label: "LOW" },
  info:     { chip: "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]", label: "INFO" },
  pass:     { chip: "border-emerald-500/60 bg-emerald-500/10 text-emerald-300 cyber-glow-ok", label: "OK" },
};

const SEV_ORDER: Severity[] = ["critical", "high", "medium", "low", "info", "pass"];

function statusIcon(status: Status, severity: Severity) {
  if (status === "pass") return <CheckCircle2 className="h-4 w-4 text-emerald-400" />;
  if (status === "fail") {
    return <XCircle className="h-4 w-4 text-red-400" />;
  }
  if (status === "warn") {
    if (severity === "high" || severity === "critical") {
      return <XCircle className="h-4 w-4 text-orange-300" />;
    }
    return <AlertTriangle className="h-4 w-4 text-yellow-300" />;
  }
  if (status === "skip") return <ShieldOff className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />;
  if (status === "info") return <CircleDashed className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />;
  return <InfoIcon className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />;
}

function scoreColor(percent: number): string {
  if (percent >= 90) return "text-emerald-300 cyber-glow-ok";
  if (percent >= 75) return "text-sky-300";
  if (percent >= 60) return "text-yellow-200";
  if (percent >= 40) return "text-orange-300";
  return "text-red-300";
}

function gradeFor(percent: number): string {
  if (percent >= 90) return "A";
  if (percent >= 75) return "B";
  if (percent >= 60) return "C";
  if (percent >= 40) return "D";
  return "F";
}

function gradeColor(grade: string): string {
  if (grade === "A") return "border-emerald-500/60 text-emerald-300 cyber-glow-ok";
  if (grade === "B") return "border-sky-500/60 text-sky-300";
  if (grade === "C") return "border-yellow-500/60 text-yellow-200";
  if (grade === "D") return "border-orange-500/60 text-orange-300";
  return "border-red-500/60 text-red-300";
}

// Translate a HardeningCheck into the same shape Tailscale/Tor audits use,
// so the layout/code below can stay isomorphic with the other two panels.
function statusOf(c: HardeningCheck): Status {
  if (c.status === "error") return "fail";
  if (c.status === "skipped") return "skip";
  if (c.status === "needs_probe") return "info";
  // status === "ready"
  if (c.points >= c.max_points) return "pass";
  if (c.points <= 0) return "fail";
  return "warn";
}

function severityOf(c: HardeningCheck): Severity {
  if (c.status === "error") return "high";
  if (c.status === "skipped" || c.status === "needs_probe") return "info";
  if (c.points >= c.max_points) return "pass";
  // Severity scales with how much of the check's weight was missed.
  const missed = c.max_points - Math.max(0, c.points);
  if (missed >= 10) return "high";
  if (missed >= 5)  return "medium";
  return "low";
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
 * Hardening device audit panel — same visual vocabulary as
 * TailscaleAuditPanel / TorAudit so the Sécurité group reads as one
 * coherent piece :
 *   - cyber-display 5xl percent + grade card on the left
 *   - 3 CounterBox (compliant / partial / non-compliant) on the right
 *   - collapsible list of checks, fail/warn auto-expanded
 *
 * No fix button yet : Hardening checks each require their own UCI / fw3
 * mutation. Wiring them per-check is a follow-up.
 */
export default function HardeningAuditPanel() {
  const qc = useQueryClient();
  const auditQ = useQuery({
    queryKey: ["slate-hardening"],
    queryFn: getSlateHardening,
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

  // Per-check fix mutation : we track which check triggered it so the
  // spinner stays on the right card.
  const [activeFix, setActiveFix] = useState<string | null>(null);
  const fixMut = useMutation({
    mutationFn: applyHardeningFix,
    onSettled: () => {
      // Re-run the gauge + the security hub reliability shield.
      qc.invalidateQueries({ queryKey: ["slate-hardening"] });
      qc.invalidateQueries({ queryKey: ["security", "risk-score"] });
      setActiveFix(null);
    },
  });

  const sortedChecks = useMemo(() => {
    const list = auditQ.data?.checks ?? [];
    return [...list].sort(
      (a, b) =>
        SEV_ORDER.indexOf(severityOf(a)) - SEV_ORDER.indexOf(severityOf(b)),
    );
  }, [auditQ.data]);

  const counts = useMemo(() => {
    const c = auditQ.data?.checks ?? [];
    return {
      pass: c.filter((x) => statusOf(x) === "pass").length,
      warn: c.filter((x) => statusOf(x) === "warn").length,
      fail: c.filter((x) => statusOf(x) === "fail").length,
    };
  }, [auditQ.data]);

  const report = auditQ.data;
  const percent = report?.percent ?? 0;

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center gap-2 text-xs text-[color:var(--color-cyber-muted)]">
        <span className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-[2px] text-[10px] uppercase tracking-[0.18em]">
          baseline pondéré
        </span>
        {report && (
          <span className="text-[10px] uppercase tracking-[0.18em]">
            {report.checks.length} checks · {report.score} / {report.max_score} pts
          </span>
        )}
        <button
          type="button"
          onClick={() => auditQ.refetch()}
          disabled={auditQ.isFetching}
          className="ml-auto inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-50"
        >
          <RefreshCw className={cn("h-3 w-3", auditQ.isFetching && "animate-spin")} />
          {auditQ.isFetching ? "scan…" : "relancer"}
        </button>
      </div>

      {auditQ.isLoading && (
        <p className="cyber-label cyber-cursor">scan en cours…</p>
      )}

      {report && !report.reachable && (
        <div className="border border-red-500/40 bg-red-500/5 p-3 text-xs text-red-300">
          <AlertTriangle className="mr-1 inline h-3 w-3" />
          Slate injoignable — checks indisponibles.
        </div>
      )}

      {report && (
        <>
          {/* Scorecard — mirrors Tailscale/Tor exactly. */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[auto_1fr]">
            <div className="flex items-center gap-4 border border-[color:var(--color-cyber-border)] p-4">
              <div className="text-center">
                <div className={cn("cyber-display text-5xl leading-none", scoreColor(percent))}>
                  {percent}
                </div>
                <div className="mt-1 text-[9px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
                  %
                </div>
              </div>
              <div
                className={cn(
                  "flex h-16 w-16 items-center justify-center border-2 font-bold",
                  gradeColor(gradeFor(percent)),
                )}
                style={{ fontSize: "2.5rem", lineHeight: 1 }}
              >
                {gradeFor(percent)}
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3 text-xs">
              <CounterBox
                label="Compliant" value={counts.pass}
                className="border-emerald-500/40 text-emerald-300"
              />
              <CounterBox
                label="Partial" value={counts.warn}
                className="border-yellow-500/40 text-yellow-200"
              />
              <CounterBox
                label="Non-compliant" value={counts.fail}
                className="border-red-500/40 text-red-300"
              />
              <div className="col-span-3 grid grid-cols-2 gap-2 text-[10px] text-[color:var(--color-cyber-muted)]">
                <div>
                  <span className="cyber-label">total points</span>{" "}
                  <span className="font-mono">{report.score}</span>
                </div>
                <div>
                  <span className="cyber-label">max points</span>{" "}
                  <span className="font-mono">{report.max_score}</span>
                </div>
                <div>
                  <span className="cyber-label">checks</span>{" "}
                  <span className="font-mono">{report.checks.length}</span>
                </div>
                <div>
                  <span className="cyber-label">reachable</span>{" "}
                  <span className="font-mono">{report.reachable ? "oui" : "non"}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Findings list (collapsible). */}
          <div className="space-y-1.5">
            {sortedChecks.map((c) => {
              const id = c.name;
              const status = statusOf(c);
              const severity = severityOf(c);
              const isOpen =
                openIds.has(id) ||
                status === "fail" ||
                status === "warn";
              const sev = SEV_STYLE[severity];
              return (
                <div
                  key={id}
                  className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]"
                >
                  <button
                    type="button"
                    onClick={() => toggle(id)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-[color:var(--color-cyber-surface-elev)]"
                  >
                    {isOpen ? (
                      <ChevronDown className="h-3 w-3 text-[color:var(--color-cyber-muted)]" />
                    ) : (
                      <ChevronRight className="h-3 w-3 text-[color:var(--color-cyber-muted)]" />
                    )}
                    {statusIcon(status, severity)}
                    <span className="flex-1 text-xs text-[color:var(--color-cyber-fg)]">
                      {c.name}
                    </span>
                    <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
                      {c.points}/{c.max_points} pts
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
                          {c.note || "—"}
                        </span>
                      </div>
                      <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
                        status: <span className="font-mono">{c.status}</span>
                      </div>
                      {/* Auto-fix : only when the backend declared the
                          check fixable AND the current status warrants
                          it. The button calls /api/slate/hardening/fix
                          which routes to the adoption-task that already
                          knows how to set the right UCI + restart the
                          right service. */}
                      {c.fix_available && (status === "fail" || status === "warn") && (
                        <div className="mt-2 flex items-start justify-between gap-2 border border-[color:var(--color-cyber-accent)]/40 bg-[color:var(--color-cyber-accent)]/5 p-2">
                          <div>
                            <span className="cyber-label text-[color:var(--color-cyber-accent)]">
                              fix automatique disponible
                            </span>
                            <div className="mt-1 text-[10px] text-[color:var(--color-cyber-muted)]">
                              Rejoue la tâche de durcissement correspondante (idempotent).
                            </div>
                            {fixMut.isError && activeFix === c.name && (
                              <div className="mt-2 text-[10px] text-red-300">
                                <AlertTriangle className="mr-1 inline h-3 w-3" />
                                {errorMessage(fixMut.error)}
                              </div>
                            )}
                            {fixMut.isSuccess && activeFix === null && fixMut.data?.ok && (
                              <div className="mt-2 text-[10px] text-emerald-300">
                                <CheckCircle2 className="mr-1 inline h-3 w-3" />
                                {fixMut.data.message || "Fix appliqué"} — relance du scan…
                              </div>
                            )}
                          </div>
                          <button
                            type="button"
                            onClick={() => {
                              setActiveFix(c.name);
                              fixMut.mutate(c.name);
                            }}
                            disabled={fixMut.isPending}
                            className="inline-flex shrink-0 items-center gap-1 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-2 py-1 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-50"
                          >
                            {fixMut.isPending && activeFix === c.name ? (
                              <>
                                <Loader2 className="h-3 w-3 animate-spin" />
                                fix…
                              </>
                            ) : (
                              <>
                                <Wrench className="h-3 w-3" />
                                Corriger
                              </>
                            )}
                          </button>
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
