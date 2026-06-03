/**
 * Location source for WiFi scans.
 *
 *  - "none"      → no position attached to scans (privacy default)
 *  - "browser"   → fetch the browser's geolocation on each scan
 *  - "gps_slate" → fetch position from the Slate's GPS dongle (when present)
 *  - "manual"    → fixed lat/lon entered by the operator
 *
 * The source + manual coords + remembered browser fix all live in
 * localStorage so the operator's choice survives reloads. Components
 * call ``resolve()`` right before a scan to materialise a final
 * ``{lat, lon, accuracy_m, source}`` or null (when the source said no).
 */

import { useCallback, useEffect, useState } from "react";

export type LocationSource = "none" | "browser" | "gps_slate" | "manual";

export interface ResolvedLocation {
  lat: number;
  lon: number;
  accuracy_m: number | null;
  source: LocationSource;
}

const STORAGE_KEY = "slate-scan-location";

interface PersistedState {
  source: LocationSource;
  manual_lat: number | null;
  manual_lon: number | null;
}

function readStored(): PersistedState {
  if (typeof window === "undefined") {
    return { source: "none", manual_lat: null, manual_lon: null };
  }
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    if (!v) return { source: "none", manual_lat: null, manual_lon: null };
    const parsed = JSON.parse(v);
    return {
      source: ["none", "browser", "gps_slate", "manual"].includes(parsed.source)
        ? parsed.source
        : "none",
      manual_lat: typeof parsed.manual_lat === "number" ? parsed.manual_lat : null,
      manual_lon: typeof parsed.manual_lon === "number" ? parsed.manual_lon : null,
    };
  } catch {
    return { source: "none", manual_lat: null, manual_lon: null };
  }
}

function persist(s: PersistedState): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* localStorage full — silent fail */
  }
}

export function useScanLocation(): {
  source: LocationSource;
  manualLat: number | null;
  manualLon: number | null;
  setSource: (s: LocationSource) => void;
  setManual: (lat: number, lon: number) => void;
  clearManual: () => void;
  /** Resolve the active source to a {lat, lon, accuracy_m, source}. Returns
   *  null when source="none" or when the source can't yield a fix. */
  resolve: () => Promise<ResolvedLocation | null>;
} {
  const [state, setState] = useState<PersistedState>(readStored);

  useEffect(() => {
    persist(state);
  }, [state]);

  const setSource = useCallback((src: LocationSource) => {
    setState((prev) => ({ ...prev, source: src }));
  }, []);

  const setManual = useCallback((lat: number, lon: number) => {
    setState((prev) => ({ ...prev, manual_lat: lat, manual_lon: lon }));
  }, []);

  const clearManual = useCallback(() => {
    setState((prev) => ({ ...prev, manual_lat: null, manual_lon: null }));
  }, []);

  const resolve = useCallback(async (): Promise<ResolvedLocation | null> => {
    const cur = state;
    if (cur.source === "none") return null;
    if (cur.source === "manual") {
      if (cur.manual_lat == null || cur.manual_lon == null) return null;
      return {
        lat: cur.manual_lat,
        lon: cur.manual_lon,
        accuracy_m: null,
        source: "manual",
      };
    }
    if (cur.source === "browser") {
      if (typeof navigator === "undefined" || !navigator.geolocation) {
        return null;
      }
      return new Promise<ResolvedLocation | null>((resolve_) => {
        navigator.geolocation.getCurrentPosition(
          (pos) =>
            resolve_({
              lat: pos.coords.latitude,
              lon: pos.coords.longitude,
              accuracy_m: pos.coords.accuracy,
              source: "browser",
            }),
          () => resolve_(null),
          { enableHighAccuracy: true, timeout: 8000, maximumAge: 30_000 },
        );
      });
    }
    if (cur.source === "gps_slate") {
      // Hit the controller's GPS endpoint. Fails open (returns null)
      // when the Slate has no dongle / gpsd isn't running.
      try {
        const resp = await fetch("/api/wifi/gps/current", {
          credentials: "include",
        });
        if (!resp.ok) return null;
        const data = await resp.json();
        if (typeof data.lat !== "number" || typeof data.lon !== "number") {
          return null;
        }
        return {
          lat: data.lat,
          lon: data.lon,
          accuracy_m: typeof data.accuracy_m === "number" ? data.accuracy_m : null,
          source: "gps_slate",
        };
      } catch {
        return null;
      }
    }
    return null;
  }, [state]);

  return {
    source: state.source,
    manualLat: state.manual_lat,
    manualLon: state.manual_lon,
    setSource,
    setManual,
    clearManual,
    resolve,
  };
}
