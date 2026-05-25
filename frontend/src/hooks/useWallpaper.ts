/**
 * Fetch a profile's wallpaper as an object URL.
 *
 * Why not a plain `<img src=... >` URL? The wallpaper endpoint requires the
 * JWT Bearer header — and `<img>` can't send arbitrary headers. So we fetch
 * the bytes with axios (which carries the auth token from the global
 * client), then materialise them with URL.createObjectURL().
 *
 * The object URL is revoked on unmount / arg change so we don't leak blob
 * memory if the user navigates between many profiles.
 */

import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { wallpaperUrl } from "@/api/profiles";
import type { WallpaperKind } from "@/types/profile";

export function useWallpaperBlobUrl(
  profileName: string | null | undefined,
  hasWallpaper: boolean | undefined,
  kind: WallpaperKind = "home",
  // Bump this to force re-fetch (e.g. after a successful upload). Passing
  // the wallpaper's uploaded_at timestamp works well.
  version?: string | null,
): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!profileName || !hasWallpaper) {
      setUrl(null);
      return;
    }
    let cancelled = false;
    let currentObjectUrl: string | null = null;
    api
      .get(wallpaperUrl(profileName, kind, version), { responseType: "blob" })
      .then(({ data }) => {
        if (cancelled) return;
        currentObjectUrl = URL.createObjectURL(data);
        setUrl(currentObjectUrl);
      })
      .catch(() => {
        if (!cancelled) setUrl(null);
      });
    return () => {
      cancelled = true;
      if (currentObjectUrl) {
        URL.revokeObjectURL(currentObjectUrl);
      }
    };
  }, [profileName, hasWallpaper, kind, version]);

  return url;
}
