import { useEffect, useState } from "react";
import { Download, QrCode, Smartphone, X } from "lucide-react";
import { api } from "@/api/client";
import { createPortal } from "react-dom";

interface Props {
  slug: string;
  ssidName: string;
  onClose: () => void;
}

/** Fetches a fresh WiFi QR PNG and shows it in a modal. */
export default function WifiQRModal({ slug, ssidName, onClose }: Props) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetch as blob (Authorization header) → convert to object URL.
  useEffect(() => {
    let revoked = false;
    let url: string | null = null;
    (async () => {
      try {
        const resp = await api.get(`/api/wifi/${encodeURIComponent(slug)}/qr`, {
          responseType: "blob",
        });
        url = URL.createObjectURL(resp.data);
        if (!revoked) setSrc(url);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Erreur QR");
      }
    })();
    return () => {
      revoked = true;
      if (url) URL.revokeObjectURL(url);
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

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[color:var(--color-cyber-bg)]/85 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="cyber-card cyber-card-accent w-full max-w-sm">
        <header className="flex items-start justify-between gap-4 border-b border-[color:var(--color-cyber-border)] p-4">
          <div>
            <div className="cyber-label mb-1 flex items-center gap-2">
              <QrCode className="cyber-glow h-3 w-3" />
              wifi qr code
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

        <div className="p-5">
          {error && (
            <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
              {error}
            </p>
          )}
          {!src && !error && (
            <p className="cyber-label cyber-cursor text-[10px]">generating</p>
          )}
          {src && (
            <div className="flex flex-col items-center gap-3">
              <div className="border border-[color:var(--color-cyber-border-strong)] bg-white p-3">
                <img
                  src={src}
                  alt={`QR code WiFi pour ${ssidName}`}
                  className="block h-64 w-64"
                />
              </div>
              <p className="flex items-center gap-2 text-[10px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
                <Smartphone className="h-3 w-3" />
                Scanne avec ton téléphone (caméra)
              </p>
              <a
                href={src}
                download={`${slug}-wifi-qr.png`}
                className="cyber-button-ghost inline-flex items-center gap-2 px-3 py-1.5 text-[10px]"
              >
                <Download className="h-3 w-3" />
                Télécharger
              </a>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
