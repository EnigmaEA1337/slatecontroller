import { FormEvent, useId, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation } from "@tanstack/react-query";
import { AlertOctagon, ShieldAlert, X } from "lucide-react";

import { factoryResetDevice } from "@/api/devices";
import { useModalA11y } from "@/hooks/useModalA11y";
import { errorMessage } from "@/lib/error-utils";

/**
 * Destructive-action modal for factory-resetting a Slate.
 *
 * UX safeguards :
 *   - Bright red border + AlertOctagon icon to scream "danger".
 *   - The submit button stays disabled until the operator types the device
 *     slug EXACTLY (GitHub-style typed confirmation).
 *   - Full backdrop, focus trapped, ESC closes.
 *   - Lists what `firstboot && reboot` actually does so the operator can't
 *     claim they didn't know.
 */
export default function FactoryResetModal({
  deviceSlug,
  deviceLabel,
  onClose,
  onDone,
}: {
  deviceSlug: string;
  deviceLabel: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const [typed, setTyped] = useState("");
  const titleId = useId();
  const panelRef = useModalA11y<HTMLDivElement>(onClose);

  const mut = useMutation({
    mutationFn: () => factoryResetDevice(deviceSlug, typed),
    onSuccess: () => onDone(),
  });

  const matches = typed === deviceSlug;
  const canSubmit = matches && !mut.isPending;

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (canSubmit) mut.mutate();
  }

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="w-full max-w-lg rounded-lg border-2 border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-surface)] p-6 shadow-2xl focus:outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="cyber-glow flex h-10 w-10 shrink-0 items-center justify-center border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/15">
              <AlertOctagon className="h-5 w-5 text-[color:var(--color-cyber-accent)]" />
            </div>
            <div>
              <h2
                id={titleId}
                className="cyber-display cyber-glow text-lg text-[color:var(--color-cyber-fg)]"
              >
                Factory reset Slate
              </h2>
              <p className="text-[10px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-accent)]">
                ⚠ action destructive · non réversible
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Fermer"
            className="rounded p-1.5 text-[color:var(--color-cyber-muted)] hover:bg-[color:var(--color-cyber-surface-2)] hover:text-[color:var(--color-cyber-fg)]"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="mb-4 rounded border border-[color:var(--color-cyber-accent)]/40 bg-[color:var(--color-cyber-accent)]/8 p-3">
          <p className="mb-2 flex items-start gap-2 text-xs text-[color:var(--color-cyber-fg)]">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--color-cyber-accent)]" />
            <span>
              Lance <code className="font-mono">firstboot -y && reboot</code>{" "}
              via SSH sur le device. Effets :
            </span>
          </p>
          <ul className="ml-6 list-disc space-y-0.5 text-[11px] text-[color:var(--color-cyber-muted)]">
            <li>
              Efface <code className="font-mono">/overlay</code> : toute
              config UCI, AdGuard, dropbear keys, profils, paquets installés.
            </li>
            <li>
              Reboot vers le firmware d'usine (DHCP server sur 192.168.8.1,
              mot de passe admin par défaut).
            </li>
            <li>
              Le contrôleur perd l'accès : TLS pin invalide, clé SSH
              déployée disparue, credentials inutilisables.
            </li>
            <li>
              Côté contrôleur, le device repasse en{" "}
              <span className="font-semibold">pending</span> — re-probe + ré-adoption nécessaires.
            </li>
          </ul>
        </div>

        <form onSubmit={onSubmit}>
          <label className="mb-1 block">
            <span className="cyber-label mb-1.5 block text-[10px]">
              Taper le slug du device pour confirmer
            </span>
            <code className="mb-1 inline-block rounded bg-[color:var(--color-cyber-surface-2)] px-2 py-0.5 font-mono text-xs text-[color:var(--color-cyber-fg)]">
              {deviceSlug}
            </code>
            <input
              type="text"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={deviceSlug}
              autoComplete="off"
              spellCheck={false}
              className="mt-2 w-full rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-3 py-2 font-mono text-sm text-[color:var(--color-cyber-fg)] focus:border-[color:var(--color-cyber-accent)] focus:outline-none"
            />
          </label>
          {deviceLabel && (
            <p className="mt-1 text-[10px] text-[color:var(--color-cyber-dim)]">
              ({deviceLabel})
            </p>
          )}

          {mut.isError && (
            <div className="mt-3 rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
              {errorMessage(mut.error)}
            </div>
          )}
          {mut.isSuccess && mut.data && (
            <div className="mt-3 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-200">
              {mut.data.started ? "Reset accepté — " : "Échec : "}
              {mut.data.note}
            </div>
          )}

          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-4 py-2 text-sm uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
            >
              Annuler
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="rounded border-2 border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/15 px-4 py-2 text-sm font-bold uppercase tracking-[0.15em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/25 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {mut.isPending ? "Reset en cours…" : "Effacer le Slate"}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
