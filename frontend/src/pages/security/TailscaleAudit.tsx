import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import TailscaleAuditPanel from "@/components/TailscaleAuditPanel";
import { getTailscaleStatus } from "@/api/tailscale";

export default function SecurityTailscaleAudit() {
  // Status drives the daemonRunning prop — without it the audit can't even
  // SSH-probe the Slate's tailscale CLI, so we short-circuit upstream.
  const statusQ = useQuery({
    queryKey: ["tailscale", "status"],
    queryFn: getTailscaleStatus,
    refetchInterval: 30_000,
  });
  const daemonRunning = !!statusQ.data?.daemon_running;

  return (
    <div className="space-y-6 p-6">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="cyber-glow h-5 w-5" />
          <h1 className="cyber-display cyber-glow text-2xl">TAILSCALE AUDIT</h1>
        </div>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          Audit posture device (local via SSH) + politique tailnet (cloud via API admin avec PAT).
        </p>
      </div>
      <TailscaleAuditPanel daemonRunning={daemonRunning} />
    </div>
  );
}
