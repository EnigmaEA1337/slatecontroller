// API client for /api/wifi/reviews — per-AP operator review status,
// keyed by ap_root (the physical-AP cluster id).

import { api } from "./client";

export type ReviewStatus = "trusted" | "known" | "ignored" | "suspicious";

export interface ApReview {
  ap_root: string;
  status: ReviewStatus;
  label: string;
  note: string;
  vendor: string;
  sample_ssids: string[];
  sample_bssid: string;
  band: string;
  channel: number;
  reviewed_at: string;
  reviewed_by: string;
}

export interface ApReviewUpsert {
  status: ReviewStatus;
  label?: string;
  note?: string;
  vendor?: string;
  sample_ssids?: string[];
  sample_bssid?: string;
  band?: string;
  channel?: number;
}

export async function listApReviews(): Promise<ApReview[]> {
  const { data } = await api.get<ApReview[]>("/api/wifi/reviews");
  return data;
}

export async function upsertApReview(
  apRoot: string,
  body: ApReviewUpsert,
): Promise<ApReview> {
  const { data } = await api.put<ApReview>(
    `/api/wifi/reviews/${encodeURIComponent(apRoot)}`,
    body,
  );
  return data;
}

export async function deleteApReview(apRoot: string): Promise<void> {
  await api.delete(`/api/wifi/reviews/${encodeURIComponent(apRoot)}`);
}

// ----------------------------------------------------------------------
// Per-BSSID overrides — sit on top of the group ap_reviews. When a BSSID
// has its own review, that status wins over the group's.
// ----------------------------------------------------------------------

export interface BssidReview {
  bssid: string;
  status: ReviewStatus;
  label: string;
  note: string;
  ssid: string;
  vendor: string;
  band: string;
  channel: number;
  reviewed_at: string;
  reviewed_by: string;
}

export interface BssidReviewUpsert {
  status: ReviewStatus;
  label?: string;
  note?: string;
  ssid?: string;
  vendor?: string;
  band?: string;
  channel?: number;
}

export async function listBssidReviews(): Promise<BssidReview[]> {
  const { data } = await api.get<BssidReview[]>("/api/wifi/bssid-reviews");
  return data;
}

export async function upsertBssidReview(
  bssid: string,
  body: BssidReviewUpsert,
): Promise<BssidReview> {
  const { data } = await api.put<BssidReview>(
    `/api/wifi/bssid-reviews/${encodeURIComponent(bssid)}`,
    body,
  );
  return data;
}

export async function deleteBssidReview(bssid: string): Promise<void> {
  await api.delete(`/api/wifi/bssid-reviews/${encodeURIComponent(bssid)}`);
}

// Visual config shared across badges/modal/filter so we stay consistent.
export const REVIEW_STATUSES: ReadonlyArray<{
  value: ReviewStatus;
  label: string;
  icon: string;
  color: string;
  hint: string;
}> = [
  {
    value: "trusted",
    label: "trusted",
    icon: "✓",
    color: "#34d399",
    hint: "AP de confiance — supprime les alertes evil-twin sur ce ap_root",
  },
  {
    value: "known",
    label: "known",
    icon: "ⓘ",
    color: "#60a5fa",
    hint: "AP voisin reconnu, neutre",
  },
  {
    value: "ignored",
    label: "ignored",
    icon: "⊘",
    color: "#94a3b8",
    hint: "Masquer du tree par défaut (toujours conservé dans l'historique)",
  },
  {
    value: "suspicious",
    label: "suspicious",
    icon: "⚠",
    color: "#fbbf24",
    hint: "AP suspect à surveiller — remonte en priorité dans Air Watch",
  },
];

export function statusConfig(s: ReviewStatus | null | undefined) {
  return REVIEW_STATUSES.find((c) => c.value === s);
}
