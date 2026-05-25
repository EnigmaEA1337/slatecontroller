import { useState } from "react";
import { Dices, ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "@/lib/utils";

const ALPHANUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
// Safe symbols only — avoid quotes and shell-special chars that complicate
// pasting into routers/clients. Symbols are still allowed via toggle.
const SAFE_SYMBOLS = "!@#$%^&*-_=+";

export function generatePassword(
  length: number,
  includeSymbols: boolean,
): string {
  const charset = ALPHANUM + (includeSymbols ? SAFE_SYMBOLS : "");
  // Use a larger UintArray to avoid modulo bias toward the start of charset.
  const buf = new Uint32Array(length);
  crypto.getRandomValues(buf);
  return Array.from(buf, (n) => charset[n % charset.length]!).join("");
}

export default function PasswordGenerator({
  onGenerate,
  className,
}: {
  onGenerate: (password: string) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [length, setLength] = useState(20);
  const [symbols, setSymbols] = useState(true);

  function generate() {
    onGenerate(generatePassword(length, symbols));
  }

  return (
    <div className={cn("relative inline-flex flex-col", className)}>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={generate}
          title="Générer un mot de passe sécurisé"
          className="flex items-center gap-1 border border-[color:var(--color-cyber-accent-dim)] bg-[color:var(--color-cyber-bg-2)] px-2 py-1 text-[10px] font-bold uppercase tracking-[0.15em] text-[color:var(--color-cyber-accent)] transition hover:border-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/8"
        >
          <Dices className="h-3 w-3" />
          Generate
        </button>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          title="Options du générateur"
          className="border border-[color:var(--color-cyber-border)] p-1 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
        >
          {open ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          )}
        </button>
      </div>
      {open && (
        <div className="mt-2 space-y-2 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg)]/60 p-2 text-[10px]">
          <label className="block">
            <div className="mb-1 flex items-center justify-between">
              <span className="cyber-label text-[10px]">longueur</span>
              <span className="cyber-glow font-mono">{length}</span>
            </div>
            <input
              type="range"
              min={8}
              max={64}
              value={length}
              onChange={(e) => setLength(Number(e.target.value))}
              className="w-full accent-[color:var(--color-cyber-accent)]"
            />
          </label>
          <label className="flex items-center gap-2 text-xs uppercase tracking-[0.15em] text-[color:var(--color-cyber-fg)]">
            <input
              type="checkbox"
              checked={symbols}
              onChange={(e) => setSymbols(e.target.checked)}
              className="h-3 w-3 accent-[color:var(--color-cyber-accent)]"
            />
            symboles ({SAFE_SYMBOLS})
          </label>
        </div>
      )}
    </div>
  );
}
