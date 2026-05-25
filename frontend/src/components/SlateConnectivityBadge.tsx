import { useQuery } from "@tanstack/react-query";
import { Globe, ShieldOff } from "lucide-react";

import { getSlateConnectivity } from "@/api/devices";

/**
 * Compact badge showing which URL the controller is currently using to
 * talk to the default device (LAN / Tailscale / custom). Polls every 15s
 * so admins see the failover happen live without having to refresh.
 *
 * The badge label tries to be self-explanatory by inferring the path type
 * from the URL:
 *   - 100.x.x.x or *.ts.net → "Tailscale"
 *   - RFC1918 (192.168, 10., 172.16+) → "LAN"
 *   - everything else → the hostname (custom)
 */
export default function SlateConnectivityBadge() {
  const status = useQuery({
    queryKey: ["slate", "connectivity"],
    queryFn: () => getSlateConnectivity(false),
    refetchInterval: 15_000,
    retry: 1,
  });

  if (status.isLoading) {
    return (
      <span className="flex items-center gap-1.5 rounded border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-surface-2)]/40 px-2 py-1 text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-dim)]">
        <Globe className="h-3 w-3 animate-pulse" />
        Connecting…
      </span>
    );
  }

  if (status.isError || !status.data) {
    return (
      <span
        className="flex items-center gap-1.5 rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-[10px] uppercase tracking-wider text-red-300"
        title="Impossible de joindre /api/slate/connectivity"
      >
        <ShieldOff className="h-3 w-3" />
        Connectivity?
      </span>
    );
  }

  const active = status.data.active_url;
  const activeHost = extractHost(active);
  const allDown = !status.data.candidates.some((c) => c.reachable);

  if (allDown) {
    return (
      <span
        className="flex items-center gap-1.5 rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-[10px] uppercase tracking-wider text-red-300"
        title={`Aucune URL admin joignable (testées : ${status.data.candidates
          .map((c) => c.host)
          .join(", ")})`}
      >
        <ShieldOff className="h-3 w-3" />
        Slate hors-ligne
      </span>
    );
  }

  const label = labelFor(activeHost);
  const activeCandidate = status.data.candidates.find(
    (c) => c.url.replace(/\/$/, "") === active.replace(/\/$/, ""),
  );
  const latency = activeCandidate?.latency_ms;

  return (
    <span
      className="flex items-center gap-1.5 rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-[10px] uppercase tracking-wider text-emerald-300"
      title={`Slate joint via ${active}${latency != null ? ` (${latency} ms)` : ""}`}
    >
      <Globe className="h-3 w-3" />
      via {label}
      {latency != null && (
        <span className="font-mono text-emerald-400/70">{Math.round(latency)}ms</span>
      )}
    </span>
  );
}

function extractHost(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname;
  } catch {
    return url;
  }
}

function labelFor(host: string): string {
  if (host.startsWith("100.") || host.endsWith(".ts.net")) return "Tailscale";
  if (
    host.startsWith("192.168.") ||
    host.startsWith("10.") ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(host)
  ) {
    return "LAN";
  }
  return host;
}
