import { ReactNode, useId } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  Info,
  X,
  XCircle,
  Zap,
} from "lucide-react";

import { useModalA11y } from "@/hooks/useModalA11y";

/**
 * Generic full-screen modal for documenting the threat model of a subsystem
 * (DNS, AdGuard, Tailscale, VPN, firewall…). Provides the chrome + tab
 * machinery; each subsystem provides its own tab contents using the helper
 * components exported below (Section / Step / ChainPoint / Threat /
 * Mitigation / StatusItem).
 *
 * Style guide: write content in **impersonal voice** ("L'utilisateur",
 * "Le routeur", "Bloque…") — never "Tu / Ton Slate". This module is
 * shipped open-source so the docs must read for any operator, not the
 * specific user who deployed it.
 */
export interface ThreatTab {
  key: string;
  label: string;
  icon: typeof Eye;
  content: ReactNode;
}

export default function ThreatModelModal({
  title,
  subtitle,
  tabs,
  activeTab,
  onTabChange,
  onClose,
}: {
  title: string;
  subtitle?: string;
  tabs: ThreatTab[];
  activeTab: string;
  onTabChange: (key: string) => void;
  onClose: () => void;
}) {
  const current = tabs.find((t) => t.key === activeTab) ?? tabs[0];
  const titleId = useId();
  // ESC to close + focus trap + restore previous focus on unmount.
  const panelRef = useModalA11y<HTMLDivElement>(onClose);
  // Portal to document.body: escapes every CSS containing block that
  // ancestors might impose (the project body has `background-attachment:
  // fixed` which traps position:fixed children under some browsers,
  // de-centering the backdrop). Rendering into <body> directly is the
  // canonical modal pattern that always works regardless of layout.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] shadow-2xl focus:outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[color:var(--color-cyber-border)] p-4">
          <div>
            <h2 id={titleId} className="text-lg font-bold text-[color:var(--color-cyber-fg)]">
              {title}
            </h2>
            {subtitle && (
              <p className="mt-1 text-xs text-[color:var(--color-cyber-muted)]">{subtitle}</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            aria-label="Fermer"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex shrink-0 border-b border-slate-800 px-2">
          {tabs.map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.key}
                onClick={() => onTabChange(t.key)}
                className={`flex items-center gap-1.5 border-b-2 px-4 py-3 text-sm font-medium transition ${
                  activeTab === t.key
                    ? "border-cyan-400 text-cyan-300"
                    : "border-transparent text-slate-400 hover:text-slate-200"
                }`}
              >
                <Icon className="h-4 w-4" />
                {t.label}
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">{current.content}</div>
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------- helpers exportés ---------------------------- //

export function Section({
  title,
  intro,
}: {
  title: string;
  intro: string;
}) {
  return (
    <div className="mb-4">
      <h3 className="mb-1 text-base font-semibold text-slate-100">{title}</h3>
      <p className="text-sm text-slate-400">{intro}</p>
    </div>
  );
}

export function Step({
  icon,
  label,
  arrow,
  color,
  last = false,
}: {
  icon: ReactNode;
  label: string;
  arrow: string;
  color: string;
  last?: boolean;
}) {
  return (
    <div className="mb-1">
      <div className="flex items-center gap-2">
        {icon}
        <span className={`${color} font-semibold`}>{label}</span>
      </div>
      {!last && (
        <div className="ml-7 text-slate-500" style={{ fontSize: "11px" }}>
          ↓ {arrow}
        </div>
      )}
    </div>
  );
}

export function ChainPoint({
  label,
  icon,
  color,
  threats,
  mitigation,
}: {
  label: string;
  icon: ReactNode;
  color: "cyan" | "emerald" | "purple" | "amber";
  threats: string[];
  mitigation: string;
}) {
  const ring: Record<string, string> = {
    cyan: "border-cyan-500/30",
    emerald: "border-emerald-500/30",
    purple: "border-purple-500/30",
    amber: "border-amber-500/30",
  };
  return (
    <div className={`rounded-lg border ${ring[color]} bg-slate-800/30 p-3`}>
      <div className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-100">
        {icon}
        {label}
      </div>
      <p className="mb-1 text-[10px] font-semibold uppercase text-slate-500">
        Menaces
      </p>
      <ul className="mb-2 list-disc pl-4 text-xs text-slate-400">
        {threats.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
      <p className="text-[10px] font-semibold uppercase text-slate-500">
        Mitigation
      </p>
      <p className="text-xs text-emerald-300">{mitigation}</p>
    </div>
  );
}

export type Severity = "critical" | "high" | "medium" | "low";

export function Threat({
  icon,
  severity,
  title,
  scenario,
  impact,
  defense,
  defenseActive,
}: {
  icon: ReactNode;
  severity: Severity;
  title: string;
  scenario: string;
  impact: string;
  defense: string;
  defenseActive: boolean;
}) {
  const sevColor: Record<Severity, string> = {
    critical: "border-red-600/50 bg-red-500/10",
    high: "border-orange-600/40 bg-orange-500/5",
    medium: "border-amber-600/30 bg-amber-500/5",
    low: "border-slate-600/30 bg-slate-500/5",
  };
  return (
    <div className={`mb-3 rounded-lg border ${sevColor[severity]} p-4`}>
      <div className="mb-2 flex items-start justify-between gap-2">
        <h4 className="flex items-center gap-2 text-base font-semibold text-slate-100">
          {icon}
          {title}
        </h4>
        <div className="flex items-center gap-2">
          <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] uppercase text-slate-400">
            {severity}
          </span>
          {defenseActive ? (
            <span className="flex items-center gap-1 rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
              <CheckCircle2 className="h-3 w-3" /> couvert
            </span>
          ) : (
            <span className="flex items-center gap-1 rounded bg-red-500/20 px-1.5 py-0.5 text-[10px] font-medium text-red-300">
              <XCircle className="h-3 w-3" /> non couvert
            </span>
          )}
        </div>
      </div>
      <p className="mb-2 text-xs text-slate-300">
        <strong className="text-slate-200">Scénario : </strong>
        {scenario}
      </p>
      <p className="mb-2 text-xs text-amber-300">
        <strong>Impact : </strong>
        {impact}
      </p>
      <p className="text-xs text-emerald-300">
        <strong>Défense : </strong>
        {defense}
      </p>
    </div>
  );
}

export function Mitigation({
  icon,
  name,
  what,
  protects,
  notProtects,
  cost,
}: {
  icon: ReactNode;
  name: string;
  what: string;
  protects: string[];
  notProtects: string[];
  cost: string;
}) {
  return (
    <div className="mb-3 rounded-lg border border-slate-700 bg-slate-800/40 p-4">
      <h4 className="mb-2 flex items-center gap-2 text-base font-semibold text-slate-100">
        {icon}
        {name}
      </h4>
      <p className="mb-3 text-xs text-slate-300">{what}</p>
      <div className="mb-2 grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase text-emerald-400">
            Protège contre
          </p>
          <ul className="space-y-0.5 text-xs text-emerald-300">
            {protects.map((p) => (
              <li key={p} className="flex items-start gap-1.5">
                <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0" /> {p}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase text-amber-400">
            Ne protège pas contre
          </p>
          <ul className="space-y-0.5 text-xs text-amber-300">
            {notProtects.map((p) => (
              <li key={p} className="flex items-start gap-1.5">
                <XCircle className="mt-0.5 h-3 w-3 shrink-0" /> {p}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <p className="border-t border-slate-700 pt-2 text-[11px] italic text-slate-500">
        <Zap className="mr-1 inline h-3 w-3" />
        Coût : {cost}
      </p>
    </div>
  );
}

export function StatusItem({
  name,
  active,
  note,
}: {
  name: string;
  active: boolean;
  note: string;
}) {
  return (
    <div
      className={`flex items-start gap-3 rounded-lg border p-3 ${
        active
          ? "border-emerald-700/40 bg-emerald-500/5"
          : "border-amber-700/40 bg-amber-500/5"
      }`}
    >
      {active ? (
        <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" />
      ) : (
        <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
      )}
      <div>
        <p className="text-sm font-medium text-slate-100">{name}</p>
        <p className="mt-0.5 text-xs text-slate-400">{note}</p>
      </div>
    </div>
  );
}

export function Recommendation({
  title,
  items,
}: {
  title: string;
  items: ReactNode[];
}) {
  return (
    <div className="mt-4 rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-4 text-xs">
      <p className="mb-2 flex items-center gap-2 font-medium text-cyan-300">
        <Info className="h-4 w-4" />
        {title}
      </p>
      <ul className="ml-6 list-disc space-y-1 text-slate-300">
        {items.map((item, idx) => (
          <li key={idx}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

export function Disclaimer({ children }: { children: ReactNode }) {
  return (
    <div className="mt-4 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <p>{children}</p>
    </div>
  );
}
