import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  CheckCircle2,
  ChevronRight,
  ScanLine,
  ShieldAlert,
  ShieldCheck,
  Siren,
  Wifi,
} from "lucide-react";
import { api } from "@/api/client";
import { getRiskScore } from "@/api/security";
import { getSlateHardening } from "@/api/slate";
import { auditTailscale, getTailscaleStatus } from "@/api/tailscale";
import {
  reliabilityShieldStyle,
} from "@/components/ReliabilityShield";
import { useSecurityReliability } from "@/hooks/useSecurityReliability";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// Same color palette as the reliability shield, applied to KPI score text.
function postureColor(percent: number): string {
  if (percent >= 85) return "text-emerald-300 cyber-glow-ok";
  if (percent >= 60) return "text-orange-300";
  return "text-red-300";
}

export default function SecurityHub() {
  const t = useT();
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
  const tailscaleStatusQ = useQuery({
    queryKey: ["tailscale", "status"],
    queryFn: getTailscaleStatus,
    refetchInterval: 30_000,
  });
  const tailscaleAuditQ = useQuery({
    queryKey: ["tailscale", "audit"],
    queryFn: auditTailscale,
    enabled: !!tailscaleStatusQ.data?.daemon_running,
    refetchOnWindowFocus: false,
    staleTime: 5 * 60 * 1000,
  });
  const torAuditQ = useQuery({
    queryKey: ["security", "tor-audit"],
    queryFn: async () => {
      const { data } = await api.get<{
        score: number;
        tor_installed: boolean;
        tor_running: boolean;
        transparent_networks: string[];
        findings: { label: string; status: string; severity: string }[];
      }>("/api/tor/audit");
      return data;
    },
    refetchOnWindowFocus: false,
    staleTime: 5 * 60 * 1000,
  });

  const reliability = useSecurityReliability();
  const shieldStyle = reliabilityShieldStyle(reliability.status);
  const ShieldIcon = shieldStyle.Icon;

  // Posture % per component (vulnerabilities is inverted from risk score).
  const hardeningPct = hardeningQ.data?.percent;
  const vulnPct = riskQ.data ? Math.max(0, 100 - riskQ.data.score) : undefined;
  const tailscalePct = tailscaleAuditQ.data?.score;
  const torPct = torAuditQ.data?.score;

  return (
    <div className="space-y-6 p-6">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <ScanLine className="cyber-glow h-5 w-5" />
          <h1 className="cyber-display cyber-glow text-2xl">
            {t("security.hub_title").toUpperCase()}
          </h1>
        </div>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          {t("security.hub_subtitle")}
        </p>
      </div>

      {/* Reliability shield + priority actions — explicit 2-column
          grid (not flex-wrap) so both column headers ("FIABILITÉ SLATE"
          and "ACTIONS PRIORITAIRES") sit at the same Y by construction.
          Grid items naturally top-align in their cell. */}
      <section
        className={cn(
          "cyber-panel grid grid-cols-1 gap-6 p-6 md:grid-cols-[auto_1fr]",
          shieldStyle.border,
          shieldStyle.bg,
        )}
      >
        {/* LEFT cell : shield + score column (its own internal flex). */}
        <div className="flex items-start gap-6">
          <ShieldIcon className={cn("h-20 w-20 shrink-0", shieldStyle.text)} />
          <div className="flex flex-col">
            <div className="cyber-label text-[10px]">
              {t("security.reliability_label")}
            </div>
            <div className="mt-1 flex items-baseline gap-2">
              <span
                className={cn(
                  "cyber-display text-6xl leading-none",
                  shieldStyle.text,
                )}
              >
                {reliability.percent !== null ? `${reliability.percent}%` : "—"}
              </span>
              <span
                className={cn(
                  "border px-2 py-[1px] text-[10px] font-bold uppercase tracking-[0.18em]",
                  shieldStyle.border,
                  shieldStyle.text,
                )}
              >
                {shieldStyle.label}
              </span>
            </div>
            <div className="mt-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
              moyenne pondérée des indicateurs disponibles
            </div>
          </div>
        </div>
        {/* Actions prioritaires — top N findings high/medium across the
            3 actionable audits (Tailscale + Tor + Hardening). Each row
            jumps to the right audit page where the operator can hit
            "Corriger" on that specific finding. Vulnerabilities is
            intentionally left out : its findings are CVE rows that
            aren't auto-fixable from here. */}
        <PriorityActions
          tailscale={tailscaleAuditQ.data}
          tor={torAuditQ.data}
          hardening={hardeningQ.data}
        />
      </section>

      {/* KPI cards */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        {/* Hardening */}
        <KpiCard
          to="/security/hardening"
          icon={<ShieldAlert className="h-5 w-5" />}
          title="Hardening device"
          subtitle="durcissement OpenWrt / GL.iNet"
          loading={hardeningQ.isLoading}
          error={hardeningQ.isError}
          empty={!hardeningQ.data}
        >
          {hardeningQ.data && hardeningPct !== undefined && (
            <>
              <BigKpi value={`${hardeningPct}%`} className={postureColor(hardeningPct)} />
              <div className="mt-1 text-[10px] text-[color:var(--color-cyber-muted)]">
                {hardeningQ.data.score}/{hardeningQ.data.max_score} pts
              </div>
              <div className="mt-2 text-[10px] text-[color:var(--color-cyber-muted)]">
                {hardeningQ.data.checks.length} checks ·{" "}
                {hardeningQ.data.checks.filter((c) => c.status === "error").length} errors
              </div>
            </>
          )}
        </KpiCard>

        {/* Vulnerabilities — display as posture (100 - risk) */}
        <KpiCard
          to="/security/vulnerabilities"
          icon={<Siren className="h-5 w-5" />}
          title="Vulnérabilités"
          subtitle="SBOM + OSV + KEV + attack paths"
          loading={riskQ.isLoading}
          error={riskQ.isError}
          empty={!riskQ.data}
        >
          {riskQ.data && vulnPct !== undefined && (
            <>
              <BigKpi value={`${vulnPct}%`} className={postureColor(vulnPct)} />
              <div className="mt-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
                risk score: {riskQ.data.score} ({riskQ.data.level})
              </div>
              <div className="mt-2 text-[10px] text-[color:var(--color-cyber-muted)]">
                {riskQ.data.findings_total} findings ·{" "}
                {riskQ.data.risk_accepted_count} accepted
              </div>
            </>
          )}
        </KpiCard>

        {/* Tailscale audit */}
        <KpiCard
          to="/security/tailscale"
          icon={<ShieldCheck className="h-5 w-5" />}
          title="Tailscale audit"
          subtitle="posture device + politique tailnet"
          loading={tailscaleAuditQ.isLoading && !!tailscaleStatusQ.data?.daemon_running}
          error={tailscaleAuditQ.isError}
          empty={!tailscaleAuditQ.data}
          unavailable={
            tailscaleStatusQ.data && !tailscaleStatusQ.data.daemon_running
              ? "daemon arrêté"
              : undefined
          }
        >
          {tailscaleAuditQ.data && tailscalePct !== undefined && (
            <>
              <BigKpi
                value={`${tailscalePct}%`}
                className={postureColor(tailscalePct)}
              />
              <div className="mt-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
                grade {tailscaleAuditQ.data.grade}
              </div>
              <div className="mt-2 text-[10px] text-[color:var(--color-cyber-muted)]">
                {tailscaleAuditQ.data.pass_count} pass ·{" "}
                <span className={tailscaleAuditQ.data.warn_count ? "text-yellow-200" : ""}>
                  {tailscaleAuditQ.data.warn_count} warn
                </span>{" "}
                ·{" "}
                <span className={tailscaleAuditQ.data.fail_count ? "text-red-300" : ""}>
                  {tailscaleAuditQ.data.fail_count} fail
                </span>
              </div>
            </>
          )}
        </KpiCard>

        {/* Tor audit — same posture KPI shape as the other 3 cards. */}
        <KpiCard
          to="/security/tor-audit"
          icon={<Wifi className="h-5 w-5" />}
          title="Tor audit"
          subtitle="passerelle Tor (ports, ip6tables, kill-switch)"
          loading={torAuditQ.isLoading}
          error={torAuditQ.isError}
          empty={!torAuditQ.data}
          unavailable={
            torAuditQ.data && !torAuditQ.data.tor_installed
              ? "tor non installé"
              : torAuditQ.data && !torAuditQ.data.tor_running
              ? "daemon arrêté"
              : undefined
          }
        >
          {torAuditQ.data && torPct !== undefined && (
            <>
              <div className="flex items-baseline gap-3">
                <BigKpi
                  value={`${torPct}%`}
                  className={postureColor(torPct)}
                />
              </div>
              <div className="mt-2 text-[10px] text-[color:var(--color-cyber-muted)]">
                {torAuditQ.data.findings.filter((f) => f.status === "pass").length} pass ·{" "}
                {(() => {
                  const warn = torAuditQ.data.findings.filter((f) => f.status === "warn").length;
                  const fail = torAuditQ.data.findings.filter((f) => f.status === "fail").length;
                  return (
                    <>
                      <span className={warn ? "text-yellow-200" : ""}>{warn} warn</span>{" · "}
                      <span className={fail ? "text-red-300" : ""}>{fail} fail</span>
                    </>
                  );
                })()}
              </div>
              {torAuditQ.data.transparent_networks.length > 0 && (
                <div className="mt-1 text-[10px] text-[color:var(--color-cyber-muted)]">
                  {torAuditQ.data.transparent_networks.length} réseau(x) transparent(s)
                </div>
              )}
            </>
          )}
        </KpiCard>
      </div>
    </div>
  );
}

function BigKpi({
  value, className,
}: { value: string; className?: string }) {
  return (
    <span className={cn("cyber-display text-4xl leading-none", className)}>
      {value}
    </span>
  );
}

// ── Priority actions panel ───────────────────────────────────────────

type Priority = {
  audit: "tailscale" | "tor" | "hardening";
  label: string;
  severity: "critical" | "high" | "medium";
  to: string;
};

const SEV_BADGE: Record<Priority["severity"], string> = {
  critical: "border-red-500/60 bg-red-500/10 text-red-300",
  high:     "border-orange-500/60 bg-orange-500/10 text-orange-300",
  medium:   "border-yellow-500/60 bg-yellow-500/10 text-yellow-200",
};

const SEV_ORDER: Record<Priority["severity"], number> = {
  critical: 0, high: 1, medium: 2,
};

const AUDIT_LABEL: Record<Priority["audit"], string> = {
  tailscale: "tailscale",
  tor: "tor",
  hardening: "hardening",
};

function buildPriorities(
  tailscale: { findings?: { label: string; status: string; severity: string }[] } | undefined,
  tor: { findings?: { label: string; status: string; severity: string }[] } | undefined,
  hardening: { checks?: { name: string; status: string; points: number; max_points: number }[] } | undefined,
): Priority[] {
  const out: Priority[] = [];
  const wantSev = (s: string): s is Priority["severity"] =>
    s === "critical" || s === "high" || s === "medium";
  const wantStatus = (s: string) => s === "fail" || s === "warn";

  for (const f of tailscale?.findings ?? []) {
    if (wantStatus(f.status) && wantSev(f.severity)) {
      out.push({ audit: "tailscale", label: f.label, severity: f.severity, to: "/security/tailscale" });
    }
  }
  for (const f of tor?.findings ?? []) {
    if (wantStatus(f.status) && wantSev(f.severity)) {
      out.push({ audit: "tor", label: f.label, severity: f.severity, to: "/security/tor-audit" });
    }
  }
  for (const c of hardening?.checks ?? []) {
    // Mirror HardeningAuditPanel's status / severity derivation.
    const status =
      c.status === "error" ? "fail" :
      c.status === "ready"
        ? (c.points >= c.max_points ? "pass" : c.points <= 0 ? "fail" : "warn")
        : "info";
    if (!wantStatus(status)) continue;
    const missed = c.max_points - Math.max(0, c.points);
    const severity: "high" | "medium" | "low" =
      c.status === "error" ? "high"
      : missed >= 10 ? "high"
      : missed >= 5  ? "medium"
      : "low";
    if (severity === "low") continue;
    out.push({ audit: "hardening", label: c.name, severity, to: "/security/hardening" });
  }
  out.sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity]);
  return out.slice(0, 5);
}

function PriorityActions({
  tailscale, tor, hardening,
}: {
  tailscale: Parameters<typeof buildPriorities>[0];
  tor: Parameters<typeof buildPriorities>[1];
  hardening: Parameters<typeof buildPriorities>[2];
}) {
  const items = buildPriorities(tailscale, tor, hardening);

  return (
    <div className="ml-auto flex w-full min-w-[300px] max-w-[460px] flex-col border-l border-[color:var(--color-cyber-border)] pl-6">
      <div className="mb-2 flex items-center gap-2">
        <span className="cyber-label">actions prioritaires</span>
        <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-dim)]">
          {items.length} / top 5
        </span>
      </div>
      {items.length === 0 ? (
        <div className="border border-emerald-500/40 bg-emerald-500/5 p-3 text-[11px] text-emerald-300">
          <CheckCircle2 className="mr-1.5 inline h-3 w-3" />
          Aucune action prioritaire — tous les indicateurs en pass / info.
        </div>
      ) : (
        <ul className="space-y-1">
          {items.map((p, i) => (
            <li key={i}>
              <Link
                to={p.to}
                className="group flex items-center gap-2 border border-[color:var(--color-cyber-border)] px-2 py-1.5 text-[11px] transition hover:border-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-surface-elev)]"
              >
                <span
                  className={cn(
                    "border px-1.5 py-[1px] text-[9px] font-bold uppercase tracking-[0.18em]",
                    SEV_BADGE[p.severity],
                  )}
                >
                  {p.severity.toUpperCase()}
                </span>
                <span className="flex-1 truncate text-[color:var(--color-cyber-fg)]">
                  {p.label}
                </span>
                <span className="hidden text-[9px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] sm:inline">
                  {AUDIT_LABEL[p.audit]}
                </span>
                <ChevronRight className="h-3 w-3 shrink-0 text-[color:var(--color-cyber-muted)] transition-transform group-hover:translate-x-0.5 group-hover:text-[color:var(--color-cyber-accent)]" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function KpiCard({
  to, icon, title, subtitle, children,
  loading, error, empty, unavailable,
}: {
  to: string;
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  children?: React.ReactNode;
  loading?: boolean;
  error?: boolean;
  empty?: boolean;
  unavailable?: string;
}) {
  return (
    <Link
      to={to}
      className="cyber-panel group flex h-full flex-col gap-3 p-5 transition-all hover:border-[color:var(--color-cyber-accent)]"
    >
      {/* Title row : single-line, truncated if long, so the 4 cards line
          up at the same vertical position regardless of label width. */}
      <div className="flex min-h-[28px] items-center gap-2 text-[color:var(--color-cyber-accent)]">
        {icon}
        <h3 className="cyber-display cyber-glow truncate text-base">{title}</h3>
        <ChevronRight className="ml-auto h-4 w-4 shrink-0 text-[color:var(--color-cyber-muted)] transition-transform group-hover:translate-x-1 group-hover:text-[color:var(--color-cyber-accent)]" />
      </div>
      {/* Subtitle : fixed two-line height so the percentage row below
          aligns across cards even when one card's subtitle is short. */}
      <div className="line-clamp-2 min-h-[28px] text-[10px] uppercase tracking-[0.18em] leading-[14px] text-[color:var(--color-cyber-muted)]">
        {subtitle}
      </div>
      <div className="flex min-h-[100px] flex-col justify-end">
        {unavailable ? (
          <div className="text-[11px] text-[color:var(--color-cyber-muted)]">
            ⊘ {unavailable}
          </div>
        ) : loading ? (
          <div className="text-[11px] text-[color:var(--color-cyber-muted)]">scan…</div>
        ) : error ? (
          <div className="text-[11px] text-red-300">données indisponibles</div>
        ) : empty ? (
          <div className="text-[11px] text-[color:var(--color-cyber-muted)]">
            cliquer pour lancer
          </div>
        ) : (
          children
        )}
      </div>
    </Link>
  );
}
