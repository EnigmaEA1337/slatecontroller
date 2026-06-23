import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  CircleDashed,
  Code2,
  Cog,
  Globe,
  HardDrive,
  Image,
  Lock,
  Monitor,
  Network,
  Radio,
  ShieldAlert,
  Share2,
  Terminal,
  Wifi,
  X,
  Zap,
} from "lucide-react";
import { planProfileActivation } from "@/api/profiles";
import type {
  ActivationPlan,
  PlanActionKind,
  PlanReadiness,
  PlanStep,
  PlanSubsystem,
} from "@/types/plan";
import { cn } from "@/lib/utils";
import { createPortal } from "react-dom";

const SUBSYS_ICON: Record<PlanSubsystem, typeof Wifi> = {
  vpn: Lock,
  dns: Globe,
  firewall: ShieldAlert,
  wifi: Wifi,
  radio: Radio,
  network: Share2,
  adguard: HardDrive,
  tor: Network,
  tailscale: Network,
  screen: Monitor,
  wallpaper: Image,
  logging: Terminal,
};

const SUBSYS_LABEL: Record<PlanSubsystem, string> = {
  vpn: "VPN",
  dns: "DNS",
  firewall: "Firewall",
  wifi: "Wi-Fi",
  radio: "Radio",
  network: "Réseaux",
  adguard: "AdGuard",
  tor: "Tor",
  tailscale: "Tailscale",
  screen: "Écran LCD",
  wallpaper: "Wallpaper",
  logging: "Logging",
};

const ACTION_LABEL: Record<PlanActionKind, string> = {
  rpc: "RPC",
  uci: "UCI",
  service: "SVC",
  noop: "—",
};

const READINESS_CLASS: Record<PlanReadiness, string> = {
  ready: "cyber-chip-ok",
  needs_probe: "cyber-chip-warn",
  skipped: "",
  blocker: "cyber-chip-on",
};

const READINESS_LABEL: Record<PlanReadiness, string> = {
  ready: "ready",
  needs_probe: "needs probe",
  skipped: "skipped",
  blocker: "BLOCKER",
};

const READINESS_ICON: Record<PlanReadiness, typeof Check> = {
  ready: Check,
  needs_probe: CircleDashed,
  skipped: CircleDashed,
  blocker: AlertTriangle,
};

function StepRow({ step }: { step: PlanStep }) {
  const Icon = SUBSYS_ICON[step.subsystem];
  const ReadyIcon = READINESS_ICON[step.readiness];
  return (
    <li className="border border-[color:var(--color-cyber-border)] p-3">
      <div className="flex items-start gap-3">
        <Icon className="cyber-glow mt-0.5 h-4 w-4 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
              {SUBSYS_LABEL[step.subsystem]}
            </span>
            <span className="cyber-chip">{ACTION_LABEL[step.action_kind]}</span>
            <span
              className={cn(
                "cyber-chip flex items-center gap-1",
                READINESS_CLASS[step.readiness],
              )}
            >
              <ReadyIcon className="h-3 w-3" />
              {READINESS_LABEL[step.readiness]}
            </span>
          </div>
          <p className="mt-1.5 text-sm text-[color:var(--color-cyber-fg)]">
            {step.summary}
          </p>
          {step.note && (
            <p className="mt-1 text-[11px] italic text-[color:var(--color-cyber-muted)]">
              {step.note}
            </p>
          )}
          {Object.keys(step.target_values).length > 0 && (
            <pre className="mt-2 overflow-x-auto border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg)] p-2 text-[10px] text-[color:var(--color-cyber-muted)]">
              {JSON.stringify(step.target_values, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </li>
  );
}

function PlanContent({ plan }: { plan: ActivationPlan }) {
  // Group by subsystem in the order the agent actually runs handlers
  // (slate-ctrl dispatcher loop) — so the UI reads like a timeline.
  const order: PlanSubsystem[] = [
    "screen",
    "network",
    "radio",
    "firewall",
    "adguard",
    "wifi",
    "vpn",
    "tor",
    "tailscale",
    "wallpaper",
    "dns",
    "logging",
  ];
  const grouped: Record<PlanSubsystem, PlanStep[]> = {
    screen: [],
    network: [],
    radio: [],
    firewall: [],
    adguard: [],
    wifi: [],
    vpn: [],
    tor: [],
    tailscale: [],
    wallpaper: [],
    dns: [],
    logging: [],
  };
  for (const s of plan.steps) {
    grouped[s.subsystem].push(s);
  }

  const blockerCount = plan.steps.filter((s) => s.readiness === "blocker")
    .length;
  const probeCount = plan.steps.filter((s) => s.readiness === "needs_probe")
    .length;

  return (
    <div className="space-y-4">
      <div className="cyber-card cyber-card-accent p-4">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Zap className="cyber-glow h-3 w-3" />
          dry-run · aucune action n'a été poussée vers le slate
        </div>
        <div className="grid grid-cols-3 gap-3 text-center">
          <div>
            <div className="cyber-glow text-2xl font-bold">
              {plan.step_count}
            </div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
              steps
            </div>
          </div>
          <div>
            <div
              className={cn(
                "text-2xl font-bold",
                probeCount > 0
                  ? "cyber-glow-amber"
                  : "text-[color:var(--color-cyber-muted)]",
              )}
            >
              {probeCount}
            </div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
              needs probe
            </div>
          </div>
          <div>
            <div
              className={cn(
                "text-2xl font-bold",
                blockerCount > 0
                  ? "cyber-glow"
                  : "cyber-glow-ok",
              )}
            >
              {blockerCount}
            </div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
              blockers
            </div>
          </div>
        </div>
      </div>

      {order.map((sub) =>
        grouped[sub].length === 0 ? null : (
          <section key={sub}>
            <h4 className="cyber-label mb-2 flex items-center gap-2">
              <Cog className="cyber-glow h-3 w-3" />
              {SUBSYS_LABEL[sub]} ({grouped[sub].length})
            </h4>
            <ul className="space-y-2">
              {grouped[sub].map((s, i) => (
                <StepRow key={i} step={s} />
              ))}
            </ul>
          </section>
        ),
      )}
    </div>
  );
}

export default function PlanModal({
  profileName,
  onClose,
}: {
  profileName: string;
  onClose: () => void;
}) {
  const plan = useQuery<ActivationPlan>({
    queryKey: ["profile-plan", profileName],
    queryFn: () => planProfileActivation(profileName),
  });

  // Close on Escape
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-[color:var(--color-cyber-bg)]/85 p-4 pt-12 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="cyber-card cyber-card-accent w-full max-w-3xl">
        <header className="flex items-start justify-between gap-4 border-b border-[color:var(--color-cyber-border)] p-5">
          <div>
            <div className="cyber-label mb-1 flex items-center gap-2">
              <Code2 className="cyber-glow h-3 w-3" />
              activation plan
            </div>
            <h2 className="cyber-display cyber-glow text-xl">
              {profileName.toUpperCase()}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="border border-transparent p-2 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="max-h-[70vh] overflow-y-auto p-5">
          {plan.isLoading && (
            <p className="cyber-label cyber-cursor">computing plan</p>
          )}
          {plan.isError && (
            <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
              Erreur de génération du plan
            </p>
          )}
          {plan.data && <PlanContent plan={plan.data} />}
        </div>

        <footer className="border-t border-[color:var(--color-cyber-border)] p-4">
          <button
            type="button"
            onClick={onClose}
            className="cyber-button-ghost w-full px-4 py-2.5 text-xs"
          >
            Fermer
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
