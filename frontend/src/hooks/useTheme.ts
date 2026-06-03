/**
 * Theme persistence hook : day / night / auto.
 *
 *  - The setting lives in localStorage under "slate-theme" so the
 *    choice survives reloads and tab restarts.
 *  - The active value is mirrored to <html data-theme="..."> so CSS
 *    can pick up the right variable set from index.css.
 *  - "auto" follows the OS `prefers-color-scheme` media query — on a
 *    system flip (e.g. macOS day/night auto), the page re-renders to
 *    the matching palette without any reload, thanks to the
 *    matchMedia listener below.
 *
 *  Usage :
 *      const { theme, setTheme } = useTheme();
 *      setTheme("day" | "night" | "auto");
 */

import { useCallback, useEffect, useState } from "react";

export type Theme = "day" | "night" | "auto";

const STORAGE_KEY = "slate-theme";

function readStored(): Theme {
  if (typeof window === "undefined") return "auto";
  const v = window.localStorage.getItem(STORAGE_KEY);
  if (v === "day" || v === "night" || v === "auto") return v;
  return "auto";
}

function applyTheme(t: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", t);
}

export function useTheme(): { theme: Theme; setTheme: (t: Theme) => void } {
  const [theme, setThemeState] = useState<Theme>(readStored);

  // Apply on mount and whenever the value changes.
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // For "auto" : re-render when the OS preference flips so the rendered
  // colors stay in sync (the CSS already responds via @media query, but
  // any JS that introspects the active palette needs this signal).
  useEffect(() => {
    if (theme !== "auto") return;
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => {
      // Trigger a re-render by re-applying — CSS picks it up automatically.
      applyTheme("auto");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, t);
    }
  }, []);

  return { theme, setTheme };
}

/** Eager boot setter — call once before React mounts so the first
 *  paint already has the right palette (avoids a flash of dark on a
 *  user who picked "day"). */
export function initThemeFromStorage(): void {
  applyTheme(readStored());
}
