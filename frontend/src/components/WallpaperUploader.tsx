/**
 * Wallpaper uploader for a (profile, kind) slot.
 *
 * Each profile has two slots: 'home' (nav screen) and 'lock' (wake/lock
 * screen). They're independent — uploading to one doesn't affect the other.
 *
 * fit_mode controls how the source image gets resized onto the Slate's
 * 320×240 screen:
 *   - contain  : letterbox/pillarbox, no crop (default — recommended)
 *   - cover    : center-crop to fill (no margins, edges clipped)
 *   - stretch  : non-uniform scale (typically ugly)
 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ImagePlus,
  QrCode,
  Sparkles,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { api } from "@/api/client";
import {
  applyStudioWallpaper,
  deleteWallpaper,
  uploadWallpaper,
} from "@/api/profiles";
import { useWallpaperBlobUrl } from "@/hooks/useWallpaper";
import type {
  FitMode,
  ProfileEnvelope,
  WallpaperKind,
} from "@/types/profile";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";

const ACCEPT = ["image/png", "image/jpeg", "image/webp"] as const;
const MAX_BYTES = 5 * 1024 * 1024;

const KIND_LABEL: Record<WallpaperKind, string> = {
  home: "Écran navigation",
  lock: "Écran verrouillé",
};
const KIND_HINT: Record<WallpaperKind, string> = {
  home: "Affiché quand l'écran est actif (carte d'accueil GL.iNet).",
  lock: "Affiché quand l'écran est verrouillé (avant déverrouillage).",
};

const FIT_LABEL: Record<FitMode, string> = {
  contain: "Contain (letterbox, pas de crop)",
  cover: "Cover (remplit, peut crop)",
  stretch: "Stretch (déforme)",
};


export default function WallpaperUploader({
  profileName,
  kind,
  hasWallpaper,
  fitMode,
  version,
  showQr = false,
}: {
  profileName: string;
  kind: WallpaperKind;
  hasWallpaper: boolean;
  fitMode: FitMode;
  /** uploaded_at — cache-buster on the preview URL. */
  version?: string | null;
  /** QR-of-activation is per-profile, not per-slot; only show on one slot. */
  showQr?: boolean;
}) {
  const qc = useQueryClient();
  const previewUrl = useWallpaperBlobUrl(profileName, hasWallpaper, kind, version);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [localFitMode, setLocalFitMode] = useState<FitMode>(fitMode);
  const fileRef = useRef<HTMLInputElement>(null);

  // Sync if backend fit_mode changes externally.
  useEffect(() => {
    setLocalFitMode(fitMode);
  }, [fitMode]);

  function invalidateAll() {
    qc.invalidateQueries({ queryKey: ["profile", profileName] });
    qc.invalidateQueries({ queryKey: ["profiles"] });
    qc.invalidateQueries({ queryKey: ["profiles", "active"] });
  }

  // Cache-update helper: patch the wallpapers block in the cached envelope.
  // Same pattern across all three mutations (upload / delete / studio) so the
  // UI always reflects the mutation result without waiting for the refetch.
  function patchEnvelopeSlot(slot: { has: boolean; fit_mode?: string; uploaded_at: string | null }) {
    qc.setQueryData<ProfileEnvelope>(["profile", profileName], (old) => {
      if (!old) return old;
      return {
        ...old,
        wallpapers: {
          ...old.wallpapers,
          [kind]: {
            has: slot.has,
            fit_mode: (slot.fit_mode ?? old.wallpapers?.[kind]?.fit_mode ?? "contain") as FitMode,
            uploaded_at: slot.uploaded_at,
          },
        },
      };
    });
  }

  const upload = useMutation({
    mutationFn: ({ file, mode }: { file: File; mode: FitMode }) =>
      uploadWallpaper(profileName, kind, file, mode),
    onSuccess: (data) => {
      setError(null);
      patchEnvelopeSlot({
        has: true,
        fit_mode: data.fit_mode,
        uploaded_at: data.uploaded_at,
      });
      invalidateAll();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const del = useMutation({
    mutationFn: () => deleteWallpaper(profileName, kind),
    onSuccess: () => {
      setError(null);
      patchEnvelopeSlot({ has: false, uploaded_at: null });
      invalidateAll();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const studioApply = useMutation({
    mutationFn: () => applyStudioWallpaper(profileName, kind),
    onSuccess: (data) => {
      setError(null);
      patchEnvelopeSlot({
        has: true,
        fit_mode: data.fit_mode,
        uploaded_at: data.uploaded_at,
      });
      invalidateAll();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  // Changing fit_mode when an image exists: re-upload the same bytes with
  // the new mode. Cheap because the file is already on the user's machine
  // — we'd need to re-pick. To avoid that, we send a tiny PUT with no
  // file body? Not really supported by FastAPI's UploadFile. So: we just
  // disable fit_mode picker once uploaded, and the user must re-upload to
  // change. (Simpler UX, less code, no edge cases.)
  const fitModeLocked = hasWallpaper;

  function handleFile(file: File | null) {
    setError(null);
    if (!file) return;
    if (!ACCEPT.includes(file.type as (typeof ACCEPT)[number])) {
      setError(`Type ${file.type || "inconnu"} non supporté (png/jpeg/webp uniquement).`);
      return;
    }
    if (file.size > MAX_BYTES) {
      setError(`Fichier trop gros (${(file.size / 1024 / 1024).toFixed(1)} MiB, max 5 MiB).`);
      return;
    }
    upload.mutate({ file, mode: localFitMode });
  }

  // QR code (lazy fetch)
  const [qrSvg, setQrSvg] = useState<string | null>(null);
  const [qrOpen, setQrOpen] = useState(false);
  useEffect(() => {
    if (!qrOpen || qrSvg) return;
    let cancelled = false;
    api
      .get(`/api/profiles/${encodeURIComponent(profileName)}/activate-qr`, {
        responseType: "text",
      })
      .then(({ data }) => {
        if (!cancelled) setQrSvg(String(data));
      })
      .catch(() => {
        if (!cancelled) setQrSvg(null);
      });
    return () => {
      cancelled = true;
    };
  }, [qrOpen, qrSvg, profileName]);

  return (
    <div className="space-y-2 border border-[color:var(--color-cyber-border)] p-3">
      <div className="flex items-center gap-2">
        <span className="cyber-label text-[10px]">{KIND_LABEL[kind]}</span>
        <span className="text-[9px] text-[color:var(--color-cyber-muted)]">
          {KIND_HINT[kind]}
        </span>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          handleFile(e.dataTransfer.files[0] ?? null);
        }}
        className={cn(
          "relative flex min-h-[120px] flex-col items-center justify-center gap-2 border-2 border-dashed p-3 transition",
          dragOver
            ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/5"
            : "border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]",
        )}
      >
        {previewUrl ? (
          <>
            <img
              src={previewUrl}
              alt={`Aperçu wallpaper ${kind}`}
              className="max-h-[180px] max-w-full object-contain"
            />
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={upload.isPending}
                className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/10 disabled:opacity-50"
              >
                <ImagePlus className="h-3 w-3" />
                {upload.isPending ? "upload…" : "remplacer"}
              </button>
              <button
                type="button"
                onClick={() => studioApply.mutate()}
                disabled={studioApply.isPending}
                className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/10 disabled:opacity-50"
                title="Régénérer un wallpaper aux couleurs du thème (utilise nom + couleur du profil)"
              >
                <Sparkles className="h-3 w-3" />
                {studioApply.isPending ? "génération…" : "régénérer du thème"}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (confirm(`Supprimer le wallpaper ${kind} ?`)) {
                    del.mutate();
                  }
                }}
                disabled={del.isPending}
                className="inline-flex items-center gap-1 border border-red-500/60 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-red-300 hover:bg-red-500/10 disabled:opacity-50"
              >
                <Trash2 className="h-3 w-3" />
                supprimer
              </button>
            </div>
          </>
        ) : (
          <>
            <UploadCloud className="h-6 w-6 text-[color:var(--color-cyber-muted)]" />
            <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
              Glisse une image ou
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={upload.isPending}
                className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/10 disabled:opacity-50"
              >
                <ImagePlus className="h-3 w-3" />
                {upload.isPending ? "upload…" : "parcourir"}
              </button>
              <button
                type="button"
                onClick={() => studioApply.mutate()}
                disabled={studioApply.isPending}
                className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-50"
                title="Générer un wallpaper aux couleurs du thème (nom + couleur du profil)"
              >
                <Sparkles className="h-3 w-3" />
                {studioApply.isPending ? "génération…" : "générer du thème"}
              </button>
            </div>
            <div className="text-[9px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
              png · jpeg · webp · max 5 MiB · ou genérer auto
            </div>
          </>
        )}

        <input
          ref={fileRef}
          type="file"
          accept={ACCEPT.join(",")}
          onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
          className="hidden"
        />
      </div>

      {/* fit_mode selector */}
      <div className="flex items-center gap-2 text-[10px]">
        <label className="cyber-label">Fit mode</label>
        <select
          value={localFitMode}
          onChange={(e) => setLocalFitMode(e.target.value as FitMode)}
          disabled={fitModeLocked}
          className="flex-1 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-1.5 py-0.5 font-mono text-[10px] text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)] disabled:opacity-60"
        >
          {(["contain", "cover", "stretch"] as FitMode[]).map((m) => (
            <option key={m} value={m}>
              {FIT_LABEL[m]}
            </option>
          ))}
        </select>
        {fitModeLocked && (
          <span className="text-[9px] text-[color:var(--color-cyber-muted)]">
            (choisi à l'upload — re-uploader pour changer)
          </span>
        )}
      </div>

      {error && (
        <div className="border border-red-500/40 bg-red-500/5 p-1.5 text-[10px] text-red-300">
          <AlertTriangle className="mr-1 inline h-3 w-3" />
          {error}
        </div>
      )}

      {/* QR code (per-profile, not per-slot — show on home slot only) */}
      {showQr && (
        <div className="pt-2">
          <button
            type="button"
            onClick={() => setQrOpen((v) => !v)}
            className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
          >
            <QrCode className="h-3 w-3" />
            {qrOpen ? "masquer" : "afficher"} le QR de bascule
          </button>
          {qrOpen && (
            <div className="mt-2 flex items-start gap-3 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3">
              {qrSvg ? (
                <div
                  className="bg-white p-2"
                  style={{ width: 180, height: 180 }}
                  dangerouslySetInnerHTML={{
                    __html: qrSvg
                      .replace(/width="[^"]+"/, 'width="100%"')
                      .replace(/height="[^"]+"/, 'height="100%"'),
                  }}
                />
              ) : (
                <div className="flex h-[180px] w-[180px] items-center justify-center text-[10px] text-[color:var(--color-cyber-muted)]">
                  génération…
                </div>
              )}
              <div className="flex-1 text-[10px] text-[color:var(--color-cyber-muted)]">
                <div className="cyber-label">Scan to switch</div>
                <p className="mt-1">
                  Encode <span className="font-mono">/profiles?activate={profileName}</span>.
                  Scanné depuis ton tel, ouvre l'UI controller et active le profil.
                </p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
