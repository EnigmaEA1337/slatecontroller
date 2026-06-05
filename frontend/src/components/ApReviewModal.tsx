// Review modal — supports two scopes :
//   - "group" : review keyed by ap_root (the physical AP cluster).
//   - "bssid" : per-BSSID override on top of the group review.
//
// The same component handles both because the form is identical
// (status + label + note) — only the storage endpoint differs.

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";

import {
  type ReviewStatus,
  REVIEW_STATUSES,
  deleteApReview,
  deleteBssidReview,
  upsertApReview,
  upsertBssidReview,
} from "@/api/ap-reviews";
import { cn } from "@/lib/utils";

export type ReviewScope = "group" | "bssid";

export interface ReviewSeed {
  scope: ReviewScope;
  // Group mode : ap_root identifies the physical AP cluster.
  // BSSID mode : ap_root is still passed for display ("this BSSID belongs
  // to group …") but the storage key is `bssid`.
  ap_root: string;
  bssid?: string;
  ssid?: string;
  vendor: string;
  ssids: string[]; // for group display ; ignored in bssid mode
  bssids: string[]; // for group display ; ignored in bssid mode
  band: string;
  channel: number;
  current_status?: string | null;
  current_label?: string;
  current_note?: string;
  // BSSID mode only — shows what the group review (if any) is so the
  // operator understands what they're overriding.
  inherited_group_status?: string | null;
  inherited_group_label?: string;
}

export default function ApReviewModal({
  open,
  onClose,
  seed,
}: {
  open: boolean;
  onClose: () => void;
  seed: ReviewSeed | null;
}) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<ReviewStatus>("known");
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!seed) return;
    setStatus((seed.current_status as ReviewStatus | undefined) ?? "known");
    setLabel(seed.current_label ?? "");
    setNote(seed.current_note ?? "");
  }, [seed]);

  const saveMut = useMutation({
    mutationFn: async () => {
      if (!seed) throw new Error("no seed");
      if (seed.scope === "group") {
        return upsertApReview(seed.ap_root, {
          status,
          label,
          note,
          vendor: seed.vendor,
          sample_ssids: seed.ssids,
          sample_bssid: seed.bssids[0] ?? "",
          band: seed.band,
          channel: seed.channel,
        });
      }
      if (!seed.bssid) throw new Error("bssid mode requires a bssid");
      return upsertBssidReview(seed.bssid, {
        status,
        label,
        note,
        ssid: seed.ssid ?? "",
        vendor: seed.vendor,
        band: seed.band,
        channel: seed.channel,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ap-reviews"] });
      qc.invalidateQueries({ queryKey: ["bssid-reviews"] });
      qc.invalidateQueries({ queryKey: ["scan-history"] });
      onClose();
    },
  });

  const delMut = useMutation({
    mutationFn: async () => {
      if (!seed) throw new Error("no seed");
      if (seed.scope === "group") return deleteApReview(seed.ap_root);
      if (!seed.bssid) throw new Error("bssid mode requires a bssid");
      return deleteBssidReview(seed.bssid);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ap-reviews"] });
      qc.invalidateQueries({ queryKey: ["bssid-reviews"] });
      qc.invalidateQueries({ queryKey: ["scan-history"] });
      onClose();
    },
  });

  if (!open || !seed) return null;

  const isBssidMode = seed.scope === "bssid";

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.55)" }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-sm shadow-2xl"
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
              review · {isBssidMode ? "BSSID (override)" : "AP physique (groupe)"}
            </div>
            <div className="font-mono text-sm">
              {isBssidMode ? (
                <>
                  {seed.ssid ? (
                    <span>{seed.ssid}</span>
                  ) : (
                    <span className="italic text-[color:var(--color-cyber-muted)]">
                      &lt;hidden&gt;
                    </span>
                  )}{" "}
                  <span className="text-[color:var(--color-cyber-muted)] text-xs">
                    · {seed.bssid}
                  </span>
                </>
              ) : (
                <>
                  {seed.vendor || "vendor inconnu"}{" "}
                  <span className="text-[color:var(--color-cyber-muted)] text-xs">
                    · ch {seed.channel} · {seed.band} GHz
                  </span>
                </>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)]"
            title="Fermer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          {isBssidMode && seed.inherited_group_status && (
            <div
              className="text-[11px] p-2 border-l-2 rounded-sm"
              style={{
                borderLeftColor: "var(--color-cyber-accent)",
                background: "var(--color-cyber-bg-2)",
              }}
            >
              <span className="text-[color:var(--color-cyber-muted)]">
                Statut hérité du groupe :{" "}
              </span>
              <span className="font-mono">
                {seed.inherited_group_status}
                {seed.inherited_group_label
                  ? ` (${seed.inherited_group_label})`
                  : ""}
              </span>
              <span className="text-[color:var(--color-cyber-muted)] block mt-0.5">
                Ton override ici remplacera ce statut pour ce BSSID
                uniquement. Les autres VAPs du groupe restent inchangées.
              </span>
            </div>
          )}

          <div>
            <div className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] mb-1">
              {isBssidMode ? "bssid" : "ap_root"}
            </div>
            <code className="font-mono text-xs">
              {isBssidMode ? seed.bssid : seed.ap_root}
            </code>
            {!isBssidMode && (
              <>
                <div className="text-[10px] text-[color:var(--color-cyber-muted)] mt-1">
                  SSIDs:{" "}
                  {seed.ssids.length === 0
                    ? "(aucun)"
                    : seed.ssids.join(" · ")}
                </div>
                <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
                  BSSIDs ({seed.bssids.length}):{" "}
                  <span className="font-mono">{seed.bssids.join(", ")}</span>
                </div>
              </>
            )}
            {isBssidMode && (
              <div className="text-[10px] text-[color:var(--color-cyber-muted)] mt-1">
                AP physique : <code className="font-mono">{seed.ap_root}</code>
              </div>
            )}
          </div>

          <div>
            <div className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] mb-1">
              statut
            </div>
            <div className="grid grid-cols-2 gap-2">
              {REVIEW_STATUSES.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setStatus(opt.value)}
                  className={cn(
                    "flex flex-col items-start gap-0.5 px-3 py-2 border rounded-sm text-left text-xs",
                    status === opt.value
                      ? "border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10"
                      : "border-[color:var(--color-cyber-border)] hover:border-[color:var(--color-cyber-border-strong)]",
                  )}
                  type="button"
                >
                  <span style={{ color: opt.color }}>
                    {opt.icon} {opt.label}
                  </span>
                  <span className="text-[10px] text-[color:var(--color-cyber-muted)]">
                    {opt.hint}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)] block mb-1">
              label (court)
            </label>
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder={
                isBssidMode
                  ? "ex: SSID admin caché, VAP suspect…"
                  : "ex: UniFi salon, Box voisin, AP café…"
              }
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
              placeholder="contexte, raison du marquage, observations…"
              maxLength={512}
              rows={3}
              className="cyber-input w-full text-xs"
            />
          </div>
        </div>

        <div className="flex items-center justify-between px-4 py-3 border-t border-[color:var(--color-cyber-border)]/60">
          <button
            onClick={() => {
              if (!seed.current_status) return;
              const target = isBssidMode
                ? "cet override BSSID"
                : "cette review de groupe";
              const tail = isBssidMode
                ? "Le BSSID héritera à nouveau du statut du groupe."
                : "Le groupe repassera en état implicite « unknown ».";
              if (confirm(`Supprimer ${target} ? ${tail}`)) {
                delMut.mutate();
              }
            }}
            disabled={!seed.current_status || delMut.isPending}
            className="cyber-button-ghost px-3 py-1.5 text-xs disabled:opacity-30"
            type="button"
          >
            supprimer
          </button>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="cyber-button-ghost px-3 py-1.5 text-xs"
              type="button"
            >
              annuler
            </button>
            <button
              onClick={() => saveMut.mutate()}
              disabled={saveMut.isPending}
              className="cyber-button px-4 py-1.5 text-xs"
              type="button"
            >
              {saveMut.isPending ? "…" : "enregistrer"}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
