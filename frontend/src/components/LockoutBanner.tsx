// Global PIN-verifier lockout banner — dual source.
//
// Polls /api/security/anti-theft/lockout-status which returns BOTH :
//   - controller : our 3-tries/60s lockout (web UI PinConfirmModal)
//   - touchscreen: gl_screen on-device (5-min, polled from /etc/gl_screen/status)
//
// When either is locked the banner appears, colour-coded :
//   - controller only  → amber, "PIN verifier verrouillé"
//   - touchscreen only → red, "Slate touchscreen verrouillé"
//   - both             → red, both sources listed

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Lock, Smartphone } from "lucide-react";

import { getLockoutStatus } from "@/api/anti-theft";

export default function LockoutBanner() {
  const status = useQuery({
    queryKey: ["security", "lockout-status"],
    queryFn: () => getLockoutStatus(),
    refetchInterval: 10_000,
    retry: false,
  });

  const [localRemainingS, setLocalRemainingS] = useState(0);

  useEffect(() => {
    if (!status.data) return;
    setLocalRemainingS(status.data.controller.remaining_lock_s);
  }, [status.data?.controller.remaining_lock_s]);

  useEffect(() => {
    if (localRemainingS <= 0) return;
    const id = window.setInterval(() => {
      setLocalRemainingS((s) => Math.max(0, s - 1));
    }, 1000);
    return () => window.clearInterval(id);
  }, [localRemainingS > 0]);

  if (!status.data) return null;

  const controllerLocked = localRemainingS > 0;
  const touchscreenLocked = status.data.touchscreen.exceed_limit;

  if (!controllerLocked && !touchscreenLocked) return null;

  // Touchscreen state dominates colour : if the physical device is
  // locked, that's the bigger concern.
  const variant: "amber" | "red" = touchscreenLocked ? "red" : "amber";
  const styles =
    variant === "red"
      ? {
          background: "rgba(220, 38, 38, 0.15)",
          borderColor: "#dc2626",
          color: "#fca5a5",
        }
      : {
          background: "rgba(251, 191, 36, 0.12)",
          borderColor: "#fbbf24",
          color: "#fbbf24",
        };

  return (
    <div
      className="w-full px-4 py-2 flex items-center justify-center gap-4 text-xs font-mono flex-wrap"
      style={{
        background: styles.background,
        borderBottom: `1px solid ${styles.borderColor}`,
        color: styles.color,
      }}
    >
      {touchscreenLocked && (
        <span className="inline-flex items-center gap-1.5">
          <Smartphone className="h-3 w-3 shrink-0 animate-pulse" />
          Slate touchscreen verrouillé
          {status.data.touchscreen.exceed_count > 0 && (
            <span className="opacity-70">
              · déclenché par {status.data.touchscreen.exceed_count} échec
              {status.data.touchscreen.exceed_count > 1 ? "s" : ""}
            </span>
          )}
        </span>
      )}
      {controllerLocked && (
        <span className="inline-flex items-center gap-1.5">
          <Lock className="h-3 w-3 shrink-0 animate-pulse" />
          PIN verifier web · {localRemainingS}s restantes
          {status.data.controller.failed_count > 0 && (
            <span className="opacity-70">
              · {status.data.controller.failed_count} échec
              {status.data.controller.failed_count > 1 ? "s" : ""}
            </span>
          )}
        </span>
      )}
    </div>
  );
}
