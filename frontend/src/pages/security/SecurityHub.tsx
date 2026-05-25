import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ChevronRight,
  ScanLine,
  ShieldAlert,
  ShieldCheck,
  Siren,
} from "lucide-react";
import { getRiskScore } from "@/api/security";
import { getSlateHardening } from "@/api/slate";
import { auditTailscale, getTailscaleStatus } from "@/api/tailscale";
import {
  reliabilityShieldStyle,
} from "@/components/ReliabilityShield";
import { useSecurityReliability } from "@/hooks/useSecurityReliability";
import { cn } from "@/lib/utils";

// Same color palette as the reliability shield, applied to KPI score text.
function postureColor(percent: number): string {
  if (percent >= 85) return "text-emerald-300 cyber-glow-ok";
  if (percent >= 60) return "text-orange-300";
  return "text-red-300";
}

export default function SecurityHub() {
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

  const reliability = useSecurityReliability();
  const shieldStyle = reliabilityShieldStyle(reliability.status);
  const ShieldIcon = shieldStyle.Icon;

  // Posture % per component (vulnerabilities is inverted from risk score).
  const hardeningPct = hardeningQ.data?.percent;
  const vulnPct = riskQ.data ? Math.max(0, 100 - riskQ.data.score) : undefined;
  const tailscalePct = tailscaleAuditQ.data?.score;

  return (
    <div className="space-y-6 p-6">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <ScanLine className="cyber-glow h-5 w-5" />
          <h1 className="cyber-display cyber-glow text-2xl">SÉCURITÉ</h1>
        </div>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          Hub des indicateurs sécurité — durcissement, vulnérabilités, mesh VPN.
        </p>
      </div>

      {/* Aggregated reliability shield */}
      <section
        className={cn(
          "cyber-panel flex flex-wrap items-center gap-6 p-6",
          shieldStyle.border,
          shieldStyle.bg,
        )}
      >
        <ShieldIcon className={cn("h-20 w-20", shieldStyle.text)} />
        <div className="flex flex-col">
          <div className="cyber-label text-[10px]">Fiabilité Slate</div>
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
        <div className="ml-auto grid min-w-[260px] grid-cols-1 gap-1.5">
          {reliability.components.map((c) => (
            <div
              key={c.id}
              className="flex items-center justify-between gap-3 border border-[color:var(--color-cyber-border)] px-3 py-1.5 text-[11px]"
            >
              <span className="text-[color:var(--color-cyber-fg)]">{c.label}</span>
              <span
                className={cn(
                  "font-mono",
                  c.available
                    ? postureColor(c.percent)
                    : "text-[color:var(--color-cyber-muted)]",
                )}
              >
                {c.available ? `${c.percent}%` : "—"}
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* KPI cards */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
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
              <div className="flex items-baseline gap-3">
                <BigKpi
                  value={`${tailscalePct}%`}
                  className={postureColor(tailscalePct)}
                />
                <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
                  grade {tailscaleAuditQ.data.grade}
                </span>
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
      className="cyber-panel group flex flex-col gap-3 p-5 transition-all hover:border-[color:var(--color-cyber-accent)]"
    >
      <div className="flex items-center gap-2 text-[color:var(--color-cyber-accent)]">
        {icon}
        <h3 className="cyber-display cyber-glow text-base">{title}</h3>
        <ChevronRight className="ml-auto h-4 w-4 text-[color:var(--color-cyber-muted)] transition-transform group-hover:translate-x-1 group-hover:text-[color:var(--color-cyber-accent)]" />
      </div>
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
        {subtitle}
      </div>
      <div className="min-h-[88px]">
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
