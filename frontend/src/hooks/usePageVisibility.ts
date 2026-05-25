import { useEffect, useState } from "react";

/**
 * Tracks whether the browser tab/window is currently visible (Page
 * Visibility API). Returns `true` when the user is looking at the tab,
 * `false` when it's hidden (other tab, minimized, screen off…).
 *
 * Intended use: gate polling queries to avoid burning network/CPU when the
 * user isn't looking. Pattern:
 *
 *   const isVisible = usePageVisibility();
 *   useQuery({
 *     queryKey: [...],
 *     queryFn: ...,
 *     refetchInterval: 30_000,
 *     enabled: isVisible,
 *   });
 *
 * Note: React Query already pauses on window blur via
 * `refetchOnWindowFocus` (default true). But that only handles refetch on
 * *focus return*, not active polling. Setting `enabled: isVisible`
 * additionally stops the `refetchInterval` timer while hidden — which is
 * what we need when the user has 5 tabs open on the controller and only
 * looks at one.
 */
export function usePageVisibility(): boolean {
  const [isVisible, setIsVisible] = useState<boolean>(
    typeof document === "undefined" ? true : !document.hidden,
  );

  useEffect(() => {
    if (typeof document === "undefined") return;
    const handler = () => setIsVisible(!document.hidden);
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, []);

  return isVisible;
}
