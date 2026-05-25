import { FormEvent, useId, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Lock,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  X,
} from "lucide-react";

import {
  type PinStrength,
  type ScreenLockStatus,
  getScreenLock,
  setScreenLockAutoLock,
  setScreenLockEnabled,
  setScreenLockPin,
} from "@/api/slate";
import { useModalA11y } from "@/hooks/useModalA11y";
import { errorMessage } from "@/lib/error-utils";

/**
 * Compact widget for the device card : shows PIN screen-lock state with a
 * one-line summary + a button to open the configuration modal.
 *
 * In V1 the controller's screen-lock endpoints target the default device
 * only (singleton SlateSSH). If the device on this card is not the default
 * we render nothing — there's no per-device SSH multiplexer yet.
 */
export default function ScreenLockWidget({
  isDefault,
}: {
  isDefault: boolean;
}) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();

  const status = useQuery({
    queryKey: ["slate", "screen-lock"],
    queryFn: getScreenLock,
    enabled: isDefault,
    staleTime: 30_000,
  });

  if (!isDefault) return null;
  if (status.isLoading) {
    return (
      <div className="mt-3 flex items-center gap-2 rounded border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-3 py-2 text-xs text-[color:var(--color-cyber-muted)]">
        <Lock className="h-3.5 w-3.5 animate-pulse" />
        chargement PIN écran…
      </div>
    );
  }
  if (status.isError || !status.data) {
    return (
      <div className="mt-3 flex items-center gap-2 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
        <AlertTriangle className="h-3.5 w-3.5" />
        PIN écran indisponible (SSH ?)
      </div>
    );
  }

  const s = status.data;

  return (
    <>
      <div className="mt-3 flex items-start justify-between gap-2 rounded border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-3 py-2 text-xs">
        <StatusSummary s={s} />
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="shrink-0 rounded border border-[color:var(--color-cyber-border-strong)] px-2 py-0.5 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-fg)] hover:border-cyan-500 hover:text-cyan-300"
          title="Configurer le verrouillage écran"
        >
          Configurer
        </button>
      </div>
      {open && (
        <ScreenLockModal
          status={s}
          onClose={() => setOpen(false)}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ["slate", "screen-lock"] });
            qc.invalidateQueries({ queryKey: ["hardening"] });
          }}
        />
      )}
    </>
  );
}

// ---------------------------- helpers UI ---------------------------- //

function StatusSummary({ s }: { s: ScreenLockStatus }) {
  if (!s.has_pin) {
    return (
      <span className="flex items-center gap-2 text-[color:var(--color-cyber-accent)]">
        <ShieldOff className="h-3.5 w-3.5" />
        <span>
          <strong>Pas de PIN écran</strong> · vol physique = contrôles admin
          immédiats
        </span>
      </span>
    );
  }
  if (!s.enabled) {
    return (
      <span className="flex items-center gap-2 text-amber-300">
        <ShieldAlert className="h-3.5 w-3.5" />
        <span>
          PIN configuré ({s.pin_length} chiffres) mais{" "}
          <strong>verrouillage désactivé</strong>
        </span>
      </span>
    );
  }
  const Icon =
    s.pin_strength === "strong"
      ? ShieldCheck
      : s.pin_strength === "weak"
      ? ShieldAlert
      : Lock;
  const color =
    s.pin_strength === "strong"
      ? "text-emerald-300"
      : s.pin_strength === "weak"
      ? "text-amber-300"
      : "text-[color:var(--color-cyber-fg)]";
  return (
    <span className={`flex items-center gap-2 ${color}`}>
      <Icon className="h-3.5 w-3.5" />
      <span>
        PIN <strong>{s.pin_length} chiffres</strong> · force{" "}
        <strong>{s.pin_strength}</strong> · auto-lock {s.auto_lock_seconds}s
      </span>
    </span>
  );
}

// ---------------------------- modal ---------------------------- //

// Client-side mirror of the backend's weak-PIN list. Lets the modal display
// strength feedback while the user types, without a roundtrip per keystroke.
// Keep in sync with `app/slate/screen_lock.py::_WEAK_PINS`.
const WEAK_PINS = new Set<string>([
  "0000", "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999",
  "1234", "4321", "1212", "2121",
  "1004", "1122", "2580", "5683", "0852", "1010", "0101",
  "1980", "1990", "2000", "2001", "2010", "2020", "2024", "2025", "2026",
  "000000", "111111", "123456", "654321", "121212",
]);

function evaluatePinStrength(pin: string): PinStrength {
  if (!pin) return "none";
  if (WEAK_PINS.has(pin)) return "weak";
  if (pin.length < 4) return "weak";
  if (pin.length >= 6) return "strong";
  return "medium";
}

function ScreenLockModal({
  status,
  onClose,
  onSaved,
}: {
  status: ScreenLockStatus;
  onClose: () => void;
  onSaved: () => void;
}) {
  const titleId = useId();
  const panelRef = useModalA11y<HTMLDivElement>(onClose);
  const [pin, setPin] = useState("");
  const [showPin, setShowPin] = useState(false);
  const [enabled, setEnabled] = useState(status.enabled);
  const [autoLock, setAutoLock] = useState(status.auto_lock_seconds || 120);
  const [error, setError] = useState<string | null>(null);

  // Live strength preview as the user types — no API call.
  const livePinStrength = useMemo(() => evaluatePinStrength(pin), [pin]);

  const pinMut = useMutation({
    mutationFn: (newPin: string) => setScreenLockPin(newPin),
    onSuccess: () => {
      setError(null);
      onSaved();
      setPin("");
    },
    onError: (err) => setError(errorMessage(err)),
  });
  const enabledMut = useMutation({
    mutationFn: (v: boolean) => setScreenLockEnabled(v),
    onSuccess: () => {
      setError(null);
      onSaved();
    },
    onError: (err) => setError(errorMessage(err)),
  });
  const autoLockMut = useMutation({
    mutationFn: (secs: number) => setScreenLockAutoLock(secs),
    onSuccess: () => {
      setError(null);
      onSaved();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  function onSubmitPin(e: FormEvent) {
    e.preventDefault();
    if (!/^\d{4,8}$/.test(pin)) {
      setError("Le PIN doit faire 4 à 8 chiffres numériques.");
      return;
    }
    pinMut.mutate(pin);
  }

  // Auto-lock presets — common useful values; the input still accepts any.
  const PRESETS = [30, 60, 120, 300, 600];

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
        className="w-full max-w-lg rounded-lg border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface)] p-6 shadow-2xl focus:outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2
              id={titleId}
              className="flex items-center gap-2 text-lg font-bold text-[color:var(--color-cyber-fg)]"
            >
              <KeyRound className="h-5 w-5 text-cyan-400" />
              Verrouillage écran tactile
            </h2>
            <p className="mt-1 text-xs text-[color:var(--color-cyber-muted)]">
              PIN, auto-lock et activation du verrouillage du Slate.
            </p>
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

        {/* État courant */}
        <div className="mb-4 rounded border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface-2)] p-3 text-xs">
          <p className="cyber-label mb-1 text-[9px]">état actuel</p>
          <StatusSummary s={status} />
        </div>

        {/* Toggle enable */}
        <div className="mb-4 flex items-center justify-between gap-3 rounded border border-[color:var(--color-cyber-border)] p-3">
          <div>
            <p className="text-sm text-[color:var(--color-cyber-fg)]">
              Verrouillage actif
            </p>
            <p className="text-[11px] text-[color:var(--color-cyber-muted)]">
              {enabled
                ? "L'écran demande le PIN après auto-lock"
                : "L'écran reste déverrouillé en permanence"}
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            onClick={() => {
              const next = !enabled;
              setEnabled(next);
              enabledMut.mutate(next);
            }}
            disabled={enabledMut.isPending}
            className={`relative inline-flex h-6 w-12 items-center rounded-full transition ${
              enabled
                ? "bg-cyan-500/30 border border-cyan-500"
                : "bg-[color:var(--color-cyber-surface-2)] border border-[color:var(--color-cyber-border-strong)]"
            }`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full transition ${
                enabled
                  ? "translate-x-7 bg-cyan-300"
                  : "translate-x-1 bg-[color:var(--color-cyber-muted)]"
              }`}
            />
          </button>
        </div>

        {/* PIN entry */}
        <form onSubmit={onSubmitPin} className="mb-4">
          <label className="mb-1 block">
            <span className="cyber-label mb-1.5 block text-[10px]">
              Nouveau PIN (4-8 chiffres)
            </span>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <input
                  type={showPin ? "text" : "password"}
                  value={pin}
                  onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
                  placeholder="••••••"
                  maxLength={8}
                  inputMode="numeric"
                  autoComplete="new-password"
                  className="w-full rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-3 py-2 pr-9 font-mono text-base text-[color:var(--color-cyber-fg)] tracking-[0.3em] focus:border-cyan-500 focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => setShowPin((v) => !v)}
                  aria-label={showPin ? "Masquer le PIN" : "Afficher le PIN"}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
                >
                  {showPin ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>
              <button
                type="submit"
                disabled={pinMut.isPending || pin.length < 4}
                className="rounded border border-cyan-500/40 bg-cyan-500/15 px-3 py-2 text-xs uppercase tracking-[0.15em] text-cyan-300 hover:bg-cyan-500/25 disabled:opacity-40"
              >
                {pinMut.isPending ? "…" : "définir"}
              </button>
            </div>
          </label>
          {pin && (
            <div className="mt-2 flex items-center gap-2 text-[11px]">
              <span className="text-[color:var(--color-cyber-muted)]">Force :</span>
              <StrengthBadge strength={livePinStrength} />
              {livePinStrength === "weak" && (
                <span className="text-amber-300">
                  ← PIN trivial ou trop court
                </span>
              )}
              {livePinStrength === "strong" && (
                <span className="text-emerald-300">← ≥6 chiffres non-trivial</span>
              )}
            </div>
          )}
        </form>

        {/* Auto-lock */}
        <div className="mb-4">
          <label className="mb-1 block">
            <span className="cyber-label mb-1.5 block text-[10px]">
              Auto-lock après inactivité ({autoLock}s)
            </span>
            <input
              type="range"
              min="15"
              max="600"
              step="15"
              value={Math.min(autoLock, 600)}
              onChange={(e) => setAutoLock(parseInt(e.target.value, 10))}
              onMouseUp={(e) =>
                autoLockMut.mutate(parseInt((e.target as HTMLInputElement).value, 10))
              }
              className="w-full accent-cyan-500"
            />
          </label>
          <div className="flex flex-wrap gap-1">
            {PRESETS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  setAutoLock(s);
                  autoLockMut.mutate(s);
                }}
                className={`rounded border px-2 py-0.5 text-[10px] uppercase tracking-[0.15em] ${
                  autoLock === s
                    ? "border-cyan-500 bg-cyan-500/15 text-cyan-300"
                    : "border-[color:var(--color-cyber-border-strong)] text-[color:var(--color-cyber-muted)] hover:border-cyan-500 hover:text-cyan-300"
                }`}
              >
                {s < 60 ? `${s}s` : `${s / 60}min`}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="mb-3 flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)] px-4 py-2 text-sm uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-cyan-500 hover:text-cyan-300"
          >
            Fermer
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function StrengthBadge({ strength }: { strength: PinStrength }) {
  const palette: Record<
    PinStrength,
    { bg: string; text: string; icon: typeof CheckCircle2; label: string }
  > = {
    none: { bg: "bg-[color:var(--color-cyber-surface-2)]", text: "text-[color:var(--color-cyber-muted)]", icon: Lock, label: "—" },
    weak: { bg: "bg-amber-500/15", text: "text-amber-300", icon: ShieldAlert, label: "faible" },
    medium: { bg: "bg-[color:var(--color-cyber-surface-2)]", text: "text-[color:var(--color-cyber-fg)]", icon: Lock, label: "medium" },
    strong: { bg: "bg-emerald-500/15", text: "text-emerald-300", icon: ShieldCheck, label: "fort" },
  };
  const p = palette[strength];
  const Icon = p.icon;
  return (
    <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 ${p.bg} ${p.text}`}>
      <Icon className="h-3 w-3" />
      {p.label}
    </span>
  );
}
