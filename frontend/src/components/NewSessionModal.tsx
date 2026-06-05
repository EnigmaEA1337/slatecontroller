// New surveillance session creation modal.

import { useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import type { SurveillanceCreate } from "@/api/surveillance";
import { cn } from "@/lib/utils";

const BANDS_PRESETS: { value: string; label: string; hint: string }[] = [
  { value: "5", label: "5 GHz seul", hint: "Recommandé — DFS + portée" },
  { value: "2", label: "2.4 GHz seul", hint: "Plus de devices anciens" },
  { value: "2,5", label: "2.4 + 5", hint: "Double radio (capture max)" },
  { value: "5,6", label: "5 + 6", hint: "Wi-Fi 6E + 7" },
  { value: "2,5,6", label: "Toutes", hint: "Charge max — 3 radios en // " },
];

const INTERVAL_PRESETS: { value: number; label: string; hint: string }[] = [
  { value: 30, label: "30 s", hint: "Très dense — beaucoup de stockage" },
  { value: 60, label: "1 min", hint: "Équilibré — défaut" },
  { value: 120, label: "2 min", hint: "Léger" },
  { value: 300, label: "5 min", hint: "Surveillance lâche" },
];

export default function NewSessionModal({
  open,
  onClose,
  onSubmit,
  presets,
  submitting,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (body: SurveillanceCreate) => void;
  presets: ReadonlyArray<{ label: string; duration_s: number; hint: string }>;
  submitting: boolean;
}) {
  const [name, setName] = useState("");
  const [bands, setBands] = useState("5");
  const [durationS, setDurationS] = useState(30 * 60);
  const [intervalS, setIntervalS] = useState(60);
  const [locationLabel, setLocationLabel] = useState("");
  const [note, setNote] = useState("");

  if (!open) return null;

  // Projected count : passes per band × #bands × #intervals.
  const passesPerBand = Math.floor(durationS / intervalS);
  const totalPasses = passesPerBand * bands.split(",").length;
  // Each pass ~5 KB (20 voisins).
  const projectedKB = totalPasses * 5;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.6)" }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl rounded-sm shadow-2xl"
        style={{
          background: "var(--color-cyber-surface)",
          border: "1px solid var(--color-cyber-border)",
          color: "var(--color-cyber-fg)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-[color:var(--color-cyber-border)]/60">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)]">
              nouvelle session
            </div>
            <div className="text-sm font-mono">surveillance WiFi</div>
          </div>
          <button
            onClick={onClose}
            className="text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-4 space-y-4 max-h-[70vh] overflow-y-auto">
          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              nom de la session *
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="ex: Bureau · 2026-06-03 14h"
              maxLength={128}
              className="cyber-input w-full text-xs"
            />
          </div>

          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              bandes
            </label>
            <div className="grid grid-cols-5 gap-2">
              {BANDS_PRESETS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setBands(opt.value)}
                  type="button"
                  className={cn(
                    "text-[10px] px-2 py-2 border rounded-sm text-left",
                    bands === opt.value
                      ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10"
                      : "border-[color:var(--color-cyber-border)] hover:border-[color:var(--color-cyber-border-strong)]",
                  )}
                  title={opt.hint}
                >
                  <div className="font-mono">{opt.label}</div>
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              durée totale
            </label>
            <div className="grid grid-cols-4 gap-2">
              {presets.map((p) => (
                <button
                  key={p.duration_s}
                  onClick={() => setDurationS(p.duration_s)}
                  type="button"
                  className={cn(
                    "text-[10px] px-2 py-2 border rounded-sm text-left",
                    durationS === p.duration_s
                      ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10"
                      : "border-[color:var(--color-cyber-border)] hover:border-[color:var(--color-cyber-border-strong)]",
                  )}
                  title={p.hint}
                >
                  <div className="font-mono">{p.label}</div>
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              intervalle entre passes
            </label>
            <div className="grid grid-cols-4 gap-2">
              {INTERVAL_PRESETS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setIntervalS(opt.value)}
                  type="button"
                  className={cn(
                    "text-[10px] px-2 py-2 border rounded-sm text-left",
                    intervalS === opt.value
                      ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10"
                      : "border-[color:var(--color-cyber-border)] hover:border-[color:var(--color-cyber-border-strong)]",
                  )}
                  title={opt.hint}
                >
                  <div className="font-mono">{opt.label}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="text-[10px] text-[color:var(--color-cyber-muted)] font-mono p-2 rounded-sm border border-[color:var(--color-cyber-border)]/40">
            ≈ {totalPasses} passes au total · ~{projectedKB.toLocaleString()} KB
          </div>

          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              lieu (optionnel)
            </label>
            <input
              value={locationLabel}
              onChange={(e) => setLocationLabel(e.target.value)}
              placeholder="ex: Bureau · 3e étage"
              maxLength={128}
              className="cyber-input w-full text-xs"
            />
          </div>

          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              note
            </label>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="contexte, hypothèse de la session, objectif…"
              maxLength={1024}
              rows={3}
              className="cyber-input w-full text-xs"
            />
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-[color:var(--color-cyber-border)]/60">
          <button
            onClick={onClose}
            className="cyber-button-ghost px-3 py-1.5 text-xs"
            type="button"
          >
            annuler
          </button>
          <button
            onClick={() =>
              onSubmit({
                name: name.trim() || `Session ${new Date().toLocaleString("fr-FR")}`,
                bands,
                target_duration_s: durationS,
                interval_s: intervalS,
                location_label: locationLabel,
                note,
              })
            }
            disabled={submitting}
            className="cyber-button px-4 py-1.5 text-xs"
            type="button"
          >
            {submitting ? "…" : "▶ démarrer"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
