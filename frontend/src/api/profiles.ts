import { api } from "./client";
import type { ActivationPlan } from "@/types/plan";
import type {
  ActiveProfileResponse,
  FitMode,
  Profile,
  ProfileEnvelope,
  ProfileWallpaperMeta,
  WallpaperKind,
} from "@/types/profile";

export async function listProfiles(): Promise<ProfileEnvelope[]> {
  const { data } = await api.get<ProfileEnvelope[]>("/api/profiles");
  return data;
}

export async function getProfile(name: string): Promise<ProfileEnvelope> {
  const { data } = await api.get<ProfileEnvelope>(
    `/api/profiles/${encodeURIComponent(name)}`,
  );
  return data;
}

export async function getActiveProfile(): Promise<ActiveProfileResponse> {
  const { data } = await api.get<ActiveProfileResponse>("/api/profiles/active");
  return data;
}

export async function createProfile(profile: Profile): Promise<ProfileEnvelope> {
  const { data } = await api.post<ProfileEnvelope>("/api/profiles", profile);
  return data;
}

export async function updateProfile(
  name: string,
  profile: Profile,
): Promise<ProfileEnvelope> {
  const { data } = await api.put<ProfileEnvelope>(
    `/api/profiles/${encodeURIComponent(name)}`,
    profile,
  );
  return data;
}

export async function deleteProfile(name: string): Promise<void> {
  await api.delete(`/api/profiles/${encodeURIComponent(name)}`);
}

export async function duplicateProfile(
  name: string,
  newName: string,
): Promise<ProfileEnvelope> {
  const { data } = await api.post<ProfileEnvelope>(
    `/api/profiles/${encodeURIComponent(name)}/duplicate`,
    { new_name: newName },
  );
  return data;
}

export async function activateProfile(
  name: string,
): Promise<ActiveProfileResponse> {
  const { data } = await api.post<ActiveProfileResponse>(
    `/api/profiles/${encodeURIComponent(name)}/activate`,
  );
  return data;
}

export async function planProfileActivation(
  name: string,
): Promise<ActivationPlan> {
  const { data } = await api.post<ActivationPlan>(
    `/api/profiles/${encodeURIComponent(name)}/plan`,
  );
  return data;
}

// ---- Wallpapers --------------------------------------------------------

/**
 * URL for the wallpaper image of {kind} on {name}. `v` is the uploaded_at
 * timestamp passed as cache-buster — the backend sends Cache-Control:
 * no-store but proxies/browsers may still cache opportunistically.
 */
export function wallpaperUrl(
  name: string, kind: WallpaperKind, version?: string | null,
): string {
  const v = version ? `?v=${encodeURIComponent(version)}` : "";
  return `/api/profiles/${encodeURIComponent(name)}/wallpaper/${kind}${v}`;
}

export async function uploadWallpaper(
  name: string, kind: WallpaperKind, file: File, fit_mode: FitMode = "contain",
): Promise<ProfileWallpaperMeta> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.put<ProfileWallpaperMeta>(
    `/api/profiles/${encodeURIComponent(name)}/wallpaper/${kind}`,
    form,
    { params: { fit_mode } },
  );
  return data;
}

export async function deleteWallpaper(
  name: string, kind: WallpaperKind,
): Promise<void> {
  await api.delete(
    `/api/profiles/${encodeURIComponent(name)}/wallpaper/${kind}`,
  );
}

/**
 * Generate a cyber-themed wallpaper for the profile and save it as the
 * given slot's wallpaper. Uses the profile's name + color for accent.
 */
export async function applyStudioWallpaper(
  name: string, kind: WallpaperKind,
): Promise<ProfileWallpaperMeta> {
  // Slate font fetch on first call → 5-10s budget.
  const { data } = await api.post<ProfileWallpaperMeta>(
    `/api/profiles/${encodeURIComponent(name)}/wallpaper-studio/apply/${kind}`,
    undefined,
    { timeout: 30_000 },
  );
  return data;
}

export interface RegenerateAllResult {
  regenerated: number;
  failed: number;
  results: Array<{ profile_name: string; kind: string; size_bytes: number }>;
  errors: Array<{ profile_name: string; kind: string; error: string }>;
  pushed_active: {
    profile_name: string;
    ok: boolean;
    skipped: boolean;
    changes: string[];
    errors: string[];
  } | null;
}

/**
 * Bulk-regenerate cyber-theme wallpapers for every profile × (home + lock).
 * Overwrites existing slots — use individual apply if you want to spare a
 * particular custom upload.
 */
export async function regenerateAllWallpapers(): Promise<RegenerateAllResult> {
  // ~2-3s per profile × 5 profiles × 2 kinds ≈ 25-30s upper bound.
  const { data } = await api.post<RegenerateAllResult>(
    "/api/profiles/wallpapers/regenerate-all",
    undefined,
    { timeout: 60_000 },
  );
  return data;
}
