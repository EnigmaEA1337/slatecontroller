/**
 * Reliability shield — visual cue of the Slate's aggregated security
 * posture. Used in two places:
 *   - SecurityHub: big variant with percentage + status label
 *   - Layout sidebar: small variant (icon-only, colored)
 *
 * Color mapping is the single source of truth for "what does X% mean"
 * — keep in sync with statusFromPercent in useSecurityReliability.
 */

import { Shield, ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReliabilityStatus } from "@/hooks/useSecurityReliability";
import { cn } from "@/lib/utils";

interface ShieldStyle {
  Icon: LucideIcon;
  text: string;     // colour for the percent number
  border: string;   // border for the big variant
  bg: string;       // glow/background for the big variant
  label: string;    // FR label
}

const STYLES: Record<ReliabilityStatus, ShieldStyle> = {
  green: {
    Icon: ShieldCheck,
    text: "text-emerald-300 cyber-glow-ok",
    border: "border-emerald-500/60",
    bg: "bg-emerald-500/5",
    label: "fiable",
  },
  orange: {
    Icon: ShieldAlert,
    text: "text-orange-300",
    border: "border-orange-500/60",
    bg: "bg-orange-500/5",
    label: "à surveiller",
  },
  red: {
    Icon: ShieldX,
    text: "text-red-300",
    border: "border-red-500/60",
    bg: "bg-red-500/5",
    label: "alerte",
  },
  unknown: {
    Icon: Shield,
    text: "text-[color:var(--color-cyber-muted)]",
    border: "border-[color:var(--color-cyber-border)]",
    bg: "",
    label: "indéterminé",
  },
};

export function reliabilityShieldStyle(status: ReliabilityStatus): ShieldStyle {
  return STYLES[status];
}

export default function ReliabilityShield({
  status, size = 4,
}: {
  status: ReliabilityStatus;
  /** Tailwind size (h-N w-N) — 4 for sidebar, larger for inline badges. */
  size?: number;
}) {
  const s = STYLES[status];
  const sizeCls = `h-${size} w-${size}`;
  return <s.Icon className={cn(sizeCls, s.text)} />;
}
