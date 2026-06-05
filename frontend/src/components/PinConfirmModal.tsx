// Reusable PIN confirmation modal — drop it wherever a sensitive action
// needs the touchscreen PIN.
//
// Lifecycle :
//   1. Caller renders <PinConfirmModal open onConfirmed={fn} title="…" />
//   2. Operator types the PIN, clicks confirmer
//   3. We hit /api/slate/screen-lock/verify with anti-bruteforce semantics
//      - ok      → call onConfirmed(), close
//      - wrong   → shake + show remaining attempts
//      - locked  → show countdown until retry_after_s expires
//      - error   → surface the message
//
// Scope param is forwarded so different sensitive flows have independent
// counters (controller_verify by default ; future "encryption_unlock"…).

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Lock, ShieldCheck, X } from "lucide-react";

import { type VerifyOutcome, verifyPin } from "@/api/pin-verify";
import { cn } from "@/lib/utils";

export default function PinConfirmModal({
  open,
  onClose,
  onConfirmed,
  title = "Confirmation PIN",
  description,
  scope = "controller_verify",
}: {
  open: boolean;
  onClose: () => void;
  onConfirmed: () => void;
  title?: string;
  description?: string;
  scope?: string;
}) {
  const [pin, setPin] = useState("");
  const [outcome, setOutcome] = useState<VerifyOutcome | null>(null);
  const [lockRemainingS, setLockRemainingS] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Reset state every time the modal reopens.
  useEffect(() => {
    if (open) {
      setPin("");
      setOutcome(null);
      setLockRemainingS(0);
      // Focus the PIN field once the portal is in the DOM.
      const t = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(t);
    }
  }, [open]);

  // Lockout countdown : tick once per second, hide the form until 0.
  useEffect(() => {
    if (lockRemainingS <= 0) return;
    const id = window.setInterval(() => {
      setLockRemainingS((s) => Math.max(0, s - 1));
    }, 1000);
    return () => window.clearInterval(id);
  }, [lockRemainingS]);

  const mut = useMutation({
    mutationFn: (attempt: string) => verifyPin(attempt, scope),
    onSuccess: (res) => {
      setOutcome(res);
      if (res.kind === "ok") {
        onConfirmed();
        onClose();
        return;
      }
      if (res.kind === "locked") {
        setLockRemainingS(res.retry_after_s);
      }
      // wrong / error → just keep showing the modal with feedback.
      setPin("");
      inputRef.current?.focus();
    },
  });

  if (!open) return null;

  const locked = lockRemainingS > 0;

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.6)" }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-sm shadow-2xl"
        style={{
          background: "var(--color-cyber-surface)",
          border: "1px solid var(--color-cyber-border)",
          color: "var(--color-cyber-fg)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-[color:var(--color-cyber-border)]/60">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-[color:var(--color-cyber-accent)]" />
            <div>
              <div className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)]">
                {title}
              </div>
              <div className="text-xs font-mono">
                PIN de l'écran tactile
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
            title="Annuler"
            type="button"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-4 space-y-3">
          {description && (
            <p className="text-[11px] text-[color:var(--color-cyber-muted)]">
              {description}
            </p>
          )}

          {locked ? (
            <div
              className="flex items-start gap-2 p-3 rounded-sm border"
              style={{
                borderColor: "#fbbf24",
                background: "rgba(251, 191, 36, 0.08)",
              }}
            >
              <Lock className="h-4 w-4 text-amber-300 shrink-0 mt-0.5" />
              <div className="text-[11px]">
                <div className="text-amber-300 font-mono mb-1">
                  Verrouillé · {lockRemainingS}s restantes
                </div>
                <div className="text-[color:var(--color-cyber-muted)]">
                  Trop d'échecs. Nouvelle tentative possible dans{" "}
                  {lockRemainingS} seconde{lockRemainingS > 1 ? "s" : ""}.
                </div>
              </div>
            </div>
          ) : (
            <>
              <input
                ref={inputRef}
                type="password"
                inputMode="numeric"
                autoComplete="off"
                value={pin}
                onChange={(e) =>
                  setPin(e.target.value.replace(/[^0-9]/g, "").slice(0, 8))
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter" && pin.length >= 4) {
                    mut.mutate(pin);
                  }
                }}
                placeholder="••••"
                className={cn(
                  "cyber-input w-full text-center font-mono text-lg tracking-[0.4em]",
                  outcome?.kind === "wrong" && "shake border-amber-300",
                )}
              />

              {outcome?.kind === "wrong" && (
                <div className="flex items-center gap-2 text-[11px] text-amber-300">
                  <AlertTriangle className="h-3 w-3 shrink-0" />
                  <span>
                    PIN incorrect · {outcome.result.remaining_attempts}{" "}
                    tentative
                    {outcome.result.remaining_attempts > 1 ? "s" : ""}{" "}
                    restante
                    {outcome.result.remaining_attempts > 1 ? "s" : ""}
                  </span>
                </div>
              )}
              {outcome?.kind === "error" && (
                <div className="text-[11px] text-amber-300 font-mono">
                  {outcome.message}
                </div>
              )}
            </>
          )}
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
            onClick={() => mut.mutate(pin)}
            disabled={locked || pin.length < 4 || mut.isPending}
            className="cyber-button px-4 py-1.5 text-xs"
            type="button"
          >
            {mut.isPending ? "…" : "confirmer"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
