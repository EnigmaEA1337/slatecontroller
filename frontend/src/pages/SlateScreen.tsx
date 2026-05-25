/**
 * Live mirror of the Slate's front touchscreen.
 *
 * Used as a design reference: by seeing exactly what the panel shows
 * (including GL.iNet's UI widgets on top of the wallpaper), you can
 * design wallpapers / status messages that place text in zones the
 * widgets don't cover.
 *
 * The backend reads /dev/fb0 raw, converts RGB565 → RGB → PNG, and
 * returns landscape 320×240. Refresh is manual or auto every N seconds.
 */

import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Camera,
  Pause,
  Play,
  RefreshCw,
} from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";


export default function SlateScreen() {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(false);
  const [scale, setScale] = useState(1);
  const [lastAt, setLastAt] = useState<Date | null>(null);
  const prevUrl = useRef<string | null>(null);

  async function capture() {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get<Blob>("/api/slate/screen/snapshot", {
        responseType: "blob",
      });
      const newUrl = URL.createObjectURL(data);
      if (prevUrl.current) URL.revokeObjectURL(prevUrl.current);
      prevUrl.current = newUrl;
      setUrl(newUrl);
      setLastAt(new Date());
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }

  // First load
  useEffect(() => {
    capture();
    return () => {
      if (prevUrl.current) URL.revokeObjectURL(prevUrl.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-refresh loop
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => capture(), 2000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto]);

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-6">
        <div className="cyber-label mb-2 flex items-center gap-2">
          <Camera className="cyber-glow h-3 w-3" />
          live mirror · /dev/fb0 → png
        </div>
        <h1
          className="cyber-display cyber-glitch text-4xl"
          data-text="SLATE SCREEN"
        >
          SLATE SCREEN
        </h1>
        <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
          Capture du framebuffer ST7789 · 320×240 landscape
        </p>
      </header>

      <div className="cyber-panel space-y-4 p-5">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={capture}
            disabled={loading}
            className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-50"
          >
            <RefreshCw
              className={cn("h-3 w-3", loading && "animate-spin")}
            />
            {loading ? "capture…" : "capturer"}
          </button>
          <button
            type="button"
            onClick={() => setAuto((v) => !v)}
            className={cn(
              "inline-flex items-center gap-1 border px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em]",
              auto
                ? "border-emerald-500/60 bg-emerald-500/10 text-emerald-300"
                : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
            )}
          >
            {auto ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}
            auto (2s) {auto ? "on" : "off"}
          </button>
          <div className="ml-auto flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
            <span>zoom</span>
            {[1, 2, 3].map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setScale(s)}
                className={cn(
                  "border px-2 py-1 font-mono",
                  scale === s
                    ? "border-[color:var(--color-cyber-accent)] text-[color:var(--color-cyber-accent)]"
                    : "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]",
                )}
              >
                {s}×
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="border border-red-500/40 bg-red-500/5 p-2 text-[10px] text-red-300">
            <AlertTriangle className="mr-1 inline h-3 w-3" />
            {error}
          </div>
        )}

        {url && (
          <div className="flex flex-col items-center gap-3">
            <div
              className="border border-[color:var(--color-cyber-border)] bg-black"
              style={{ padding: 4 }}
            >
              <img
                src={url}
                alt="Slate screen"
                className="block"
                style={{
                  width: 320 * scale,
                  height: 240 * scale,
                  imageRendering: scale > 1 ? "pixelated" : "auto",
                }}
              />
            </div>
            {lastAt && (
              <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
                capturé {lastAt.toLocaleTimeString("fr-FR")} · 320×240 PNG
              </div>
            )}
          </div>
        )}

        <div className="border-t border-[color:var(--color-cyber-border)] pt-3 text-[10px] text-[color:var(--color-cyber-muted)]">
          <div className="cyber-label mb-1">comment l'utiliser</div>
          <ul className="list-disc space-y-1 pl-5">
            <li>
              Capture pour voir où les widgets GL.iNet (Internet, VPN, signal,
              batterie) se positionnent à l'écran.
            </li>
            <li>
              Identifie les "zones sûres" — coins, bandeau du haut, bandeau du
              bas — où ton texte de wallpaper ne sera pas recouvert.
            </li>
            <li>
              Mode auto pour voir les changements en live pendant que tu
              modifies la config / actives un profil.
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}
