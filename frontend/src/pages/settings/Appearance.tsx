/**
 * Settings → Apparence / Appearance
 *
 *   - Sélecteur de thème : Jour / Nuit / Automatique.
 *   - Sélecteur de langue : Français / English.
 *   - Aperçu de la palette active.
 *
 * Les deux préférences sont stockées côté navigateur (localStorage).
 * Les libellés et descriptions transitent par le système i18n maison.
 */

import { Check, Languages, Monitor, Moon, Sun } from "lucide-react";

import { type Lang, useLang } from "@/hooks/useLang";
import { type Theme, useTheme } from "@/hooks/useTheme";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

interface ThemeOption {
  value: Theme;
  labelKey: string;
  descKey: string;
  icon: typeof Sun;
}

const THEME_OPTIONS: ThemeOption[] = [
  { value: "day", labelKey: "appearance.day_label", descKey: "appearance.day_desc", icon: Sun },
  { value: "night", labelKey: "appearance.night_label", descKey: "appearance.night_desc", icon: Moon },
  { value: "auto", labelKey: "appearance.auto_label", descKey: "appearance.auto_desc", icon: Monitor },
];

interface LangOption {
  value: Lang;
  labelKey: string;
  descKey: string;
}

const LANG_OPTIONS: LangOption[] = [
  { value: "fr", labelKey: "appearance.lang_fr", descKey: "appearance.lang_fr_desc" },
  { value: "en", labelKey: "appearance.lang_en", descKey: "appearance.lang_en_desc" },
];

export default function Appearance() {
  const { theme, setTheme } = useTheme();
  const { lang, setLang } = useLang();
  const t = useT();

  return (
    <div className="space-y-6">
      <header>
        <h1 className="cyber-display cyber-glow text-2xl">
          {t("appearance.title").toUpperCase()}
        </h1>
        <p className="cyber-label text-[10px] mt-1">
          {t("appearance.subtitle")}
        </p>
      </header>

      <section className="space-y-3">
        <header className="cyber-label text-[10px]">
          {t("appearance.section_theme")}
        </header>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {THEME_OPTIONS.map((opt) => {
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
                <div className="cyber-display mt-3 text-lg">
                  {t(opt.labelKey)}
                </div>
                <p className="text-[11px] text-[color:var(--color-cyber-muted)] mt-1">
                  {t(opt.descKey)}
                </p>
              </button>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <header className="cyber-label text-[10px] flex items-center gap-2">
          <Languages className="h-3 w-3" />
          {t("appearance.section_lang")}
        </header>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {LANG_OPTIONS.map((opt) => {
            const selected = lang === opt.value;
            return (
              <button
                key={opt.value}
                onClick={() => setLang(opt.value)}
                className={cn(
                  "cyber-card relative p-4 text-left transition-all",
                  selected
                    ? "cyber-card-accent ring-2 ring-[color:var(--color-cyber-accent)]"
                    : "hover:border-[color:var(--color-cyber-accent)]",
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <span
                    className={cn(
                      "cyber-display text-2xl tracking-widest",
                      selected
                        ? "text-[color:var(--color-cyber-accent)] cyber-glow"
                        : "text-[color:var(--color-cyber-muted)]",
                    )}
                  >
                    {opt.value.toUpperCase()}
                  </span>
                  {selected && (
                    <Check className="h-4 w-4 text-[color:var(--color-cyber-accent)] cyber-glow" />
                  )}
                </div>
                <div className="cyber-display mt-3 text-lg">
                  {t(opt.labelKey)}
                </div>
                <p className="text-[11px] text-[color:var(--color-cyber-muted)] mt-1">
                  {t(opt.descKey)}
                </p>
              </button>
            );
          })}
        </div>
      </section>

      <section className="cyber-card p-4 space-y-2">
        <header className="cyber-label text-[10px]">
          {t("appearance.section_palette")}
        </header>
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
        <header className="cyber-label text-[10px] mb-2">
          {t("appearance.section_note")}
        </header>
        <p className="text-xs text-[color:var(--color-cyber-muted)]">
          {t("appearance.note_body")}
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
