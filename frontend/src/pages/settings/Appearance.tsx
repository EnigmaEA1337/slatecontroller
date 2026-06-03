/**
 * Settings → Apparence
 *
 * Theme selector : Jour / Nuit / Auto (suit le système).
 * Stored client-side in localStorage via the useTheme hook.
 */

import { Check, Monitor, Moon, Sun } from "lucide-react";
import { type Theme, useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";

interface ThemeOption {
  value: Theme;
  label: string;
  description: string;
  icon: typeof Sun;
}

const OPTIONS: ThemeOption[] = [
  {
    value: "day",
    label: "Jour",
    description: "Blanc + bleu électrique cyberpunk. Idéal en plein jour, écran en éclairage ambiant fort.",
    icon: Sun,
  },
  {
    value: "night",
    label: "Nuit",
    description: "Noir + rouge corail cyber. Le thème par défaut, idéal pour les sessions tardives.",
    icon: Moon,
  },
  {
    value: "auto",
    label: "Auto (système)",
    description: "Suit la préférence du système d'exploitation (macOS / Windows / Linux). Bascule auto si ton OS a un mode jour/nuit programmé.",
    icon: Monitor,
  },
];

export default function Appearance() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="space-y-6">
      <header>
        <h1 className="cyber-display cyber-glow text-2xl">APPARENCE</h1>
        <p className="cyber-label text-[10px] mt-1">
          Thème visuel · Variables CSS · Persistance localStorage
        </p>
      </header>

      <section className="space-y-3">
        <header className="cyber-label text-[10px]">choix du thème</header>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {OPTIONS.map((opt) => {
            const Icon = opt.icon;
            const selected = theme === opt.value;
            return (
              <button
                key={opt.value}
                onClick={() => setTheme(opt.value)}
                className={cn(
                  "cyber-card relative p-4 text-left transition-all",
                  selected
                    ? "cyber-card-accent ring-2 ring-[color:var(--color-cyber-accent)]"
                    : "hover:border-[color:var(--color-cyber-accent)]",
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <Icon
                    className={cn(
                      "h-6 w-6 shrink-0",
                      selected
                        ? "text-[color:var(--color-cyber-accent)] cyber-glow"
                        : "text-[color:var(--color-cyber-muted)]",
                    )}
                  />
                  {selected && (
                    <Check className="h-4 w-4 text-[color:var(--color-cyber-accent)] cyber-glow" />
                  )}
                </div>
                <div className="cyber-display mt-3 text-lg">{opt.label}</div>
                <p className="text-[11px] text-[color:var(--color-cyber-muted)] mt-1">
                  {opt.description}
                </p>
              </button>
            );
          })}
        </div>
      </section>

      <section className="cyber-card p-4 space-y-2">
        <header className="cyber-label text-[10px]">// preview palette</header>
        <div className="flex flex-wrap gap-2">
          <Swatch label="bg" varName="--color-cyber-bg" />
          <Swatch label="surface" varName="--color-cyber-surface" />
          <Swatch label="border" varName="--color-cyber-border" />
          <Swatch label="accent" varName="--color-cyber-accent" />
          <Swatch label="ok" varName="--color-cyber-ok" />
          <Swatch label="warn" varName="--color-cyber-warn" />
          <Swatch label="danger" varName="--color-cyber-danger" />
          <Swatch label="fg" varName="--color-cyber-fg" />
        </div>
      </section>

      <section className="cyber-card p-4">
        <header className="cyber-label text-[10px] mb-2">// note</header>
        <p className="text-xs text-[color:var(--color-cyber-muted)]">
          Le réglage est <span className="font-mono">localStorage[slate-theme]</span> côté
          navigateur — chaque opérateur a son propre choix sur sa propre machine.
          Pas de préférence stockée côté controller pour l'instant.
        </p>
      </section>
    </div>
  );
}

function Swatch({ label, varName }: { label: string; varName: string }) {
  return (
    <div className="flex flex-col items-center gap-1">
      <div
        className="h-12 w-12 rounded border border-[color:var(--color-cyber-border-strong)]"
        style={{ backgroundColor: `var(${varName})` }}
      />
      <span className="text-[10px] font-mono text-[color:var(--color-cyber-muted)]">
        {label}
      </span>
    </div>
  );
}
