/**
 * Modal "révéler le mot de passe d'un SSID".
 *
 * Sensible — on ne pré-charge pas le PSK. Le fetch ne part qu'après
 * l'ouverture de la modal (l'opérateur a explicitement cliqué l'œil)
 * et le DOM ne contient jamais le clair tant que `shown` est false.
 */

import { useEffect, useState } from "react";
import { Check, Copy, Eye, EyeOff, KeyRound, X } from "lucide-react";
import { createPortal } from "react-dom";
import { getSsidPassword } from "@/api/wifi";
import { errorMessage } from "@/lib/error-utils";

interface Props {
  slug: string;
  ssidName: string;
  onClose: () => void;
}

export default function WifiPasswordModal({ slug, ssidName, onClose }: Props) {
  const [password, setPassword] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const pw = await getSsidPassword(slug);
        if (!cancelled) setPassword(pw);
      } catch (e) {
        if (!cancelled) setError(errorMessage(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [slug]);

  // Close on Escape
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  async function copyToClipboard() {
    if (!password) return;
    try {
      await navigator.clipboard.writeText(password);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API can fail in non-HTTPS contexts — best effort.
    }
  }

  const masked = password ? "•".repeat(Math.max(password.length, 8)) : "••••••••";

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[color:var(--color-cyber-bg)]/85 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="cyber-card cyber-card-accent w-full max-w-md">
        <header className="flex items-start justify-between gap-4 border-b border-[color:var(--color-cyber-border)] p-4">
          <div>
            <div className="cyber-label mb-1 flex items-center gap-2">
              <KeyRound className="cyber-glow h-3 w-3" />
              wifi password
            </div>
            <h2 className="cyber-display cyber-glow text-base">{ssidName}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="border border-transparent p-2 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="space-y-3 p-5">
          {error && (
            <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
              {error}
            </p>
          )}
          {!password && !error && (
            <p className="cyber-label cyber-cursor text-[10px]">decrypting</p>
          )}
          {password && (
            <>
              <div className="flex items-center gap-2">
                <div className="flex-1 border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-bg-2)]/60 px-3 py-2.5 font-mono text-sm tracking-wider text-[color:var(--color-cyber-accent)]">
                  {shown ? password : masked}
                </div>
                <button
                  type="button"
                  onClick={() => setShown((v) => !v)}
                  title={shown ? "Masquer" : "Afficher"}
                  className="border border-[color:var(--color-cyber-border)] p-2.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
                >
                  {shown ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
                <button
                  type="button"
                  onClick={copyToClipboard}
                  title="Copier"
                  className="border border-[color:var(--color-cyber-border)] p-2.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
                >
                  {copied ? (
                    <Check className="h-4 w-4 text-emerald-400" />
                  ) : (
                    <Copy className="h-4 w-4" />
                  )}
                </button>
              </div>
              <p className="text-[10px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
                {shown
                  ? "// clair visible — referme la modal quand fini"
                  : "// cliquer l'œil pour révéler"}
              </p>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
