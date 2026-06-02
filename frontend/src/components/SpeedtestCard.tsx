import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Gauge, Loader2, Zap } from "lucide-react";

import { runSpeedtest, type SpeedtestResult } from "@/api/observability";
import { errorMessage } from "@/lib/error-utils";

/**
 * Cyberpunk speedtest card. The backend runs ping + curl-based download
 * + upload against Cloudflare's free endpoints over SSH on the Slate, so
 * the numbers reflect the Slate's actual WAN, not the controller host.
 *
 * Phase indicator (during the test) :
 *   1. ping     (~5 s)
 *   2. download (~10-15 s, capped at 30 s)
 *   3. upload   (~10-20 s, capped at 30 s)
 */
export default function SpeedtestCard() {
  const [phase, setPhase] = useState<"idle" | "ping" | "down" | "up" | "done">(
    "idle",
  );

  const mut = useMutation({
    mutationFn: async (): Promise<SpeedtestResult> => {
      // We don't get phase updates from the backend (one POST → blocks
      // until everything's done) — so we simulate them client-side with
      // best-effort timings. The Phase indicator is purely cosmetic.
      setPhase("ping");
      const downTimer = setTimeout(() => setPhase("down"), 6_000);
      const upTimer = setTimeout(() => setPhase("up"), 22_000);
      try {
        const res = await runSpeedtest();
        return res;
      } finally {
        clearTimeout(downTimer);
        clearTimeout(upTimer);
        setPhase("done");
      }
    },
  });

  const r = mut.data;

  return (
    <section className="cyber-card mt-6 p-5">
      <header className="mb-4 flex items-center gap-2">
        <Gauge className="cyber-glow h-3 w-3 text-[color:var(--color-cyber-accent)]" />
        <h3 className="cyber-label flex-1">Speedtest WAN</h3>
        <span className="text-[10px] uppercase tracking-[0.25em] text-[color:var(--color-cyber-dim)]">
          via Cloudflare · depuis le Slate
        </span>
        <button
          type="button"
          onClick={() => mut.mutate()}
          disabled={mut.isPending}
          className="cyber-button-ghost ml-2 inline-flex items-center gap-1.5 px-3 py-1 text-[10px] uppercase tracking-[0.25em]"
        >
          {mut.isPending ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin" />
              <PhaseLabel phase={phase} />
            </>
          ) : (
            <>
              <Zap className="h-3 w-3" />
              {r ? "Relancer" : "Lancer"}
            </>
          )}
        </button>
      </header>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <MetricTile
          label="Ping"
          unit="ms"
          value={r?.ping_ms}
          subtitle={
            r?.jitter_ms != null
              ? `± ${r.jitter_ms.toFixed(1)} ms jitter`
              : r?.packet_loss_pct != null
              ? `${r.packet_loss_pct.toFixed(1)}% loss`
              : undefined
          }
          icon={<Gauge className="h-3 w-3" />}
          digits={1}
        />
        <MetricTile
          label="Download"
          unit="Mbps"
          value={r?.download_mbps}
          subtitle={
            r?.bytes_downloaded != null
              ? `${(r.bytes_downloaded / 1e6).toFixed(0)} MB downloaded`
              : undefined
          }
          icon={<ArrowDown className="h-3 w-3" />}
          digits={1}
        />
        <MetricTile
          label="Upload"
          unit="Mbps"
          value={r?.upload_mbps}
          subtitle={
            r?.bytes_uploaded != null
              ? `${(r.bytes_uploaded / 1e6).toFixed(0)} MB uploaded`
              : undefined
          }
          icon={<ArrowUp className="h-3 w-3" />}
          digits={1}
        />
      </div>

      {mut.error && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(mut.error)}
        </p>
      )}
      {r?.error && (
        <p className="mt-3 text-xs text-[color:var(--color-cyber-warn)]">
          {r.error}
        </p>
      )}
    </section>
  );
}

function PhaseLabel({ phase }: { phase: "idle" | "ping" | "down" | "up" | "done" }) {
  if (phase === "ping") return <>ping…</>;
  if (phase === "down") return <>download…</>;
  if (phase === "up") return <>upload…</>;
  return <>en cours…</>;
}

function MetricTile({
  label,
  unit,
  value,
  subtitle,
  icon,
  digits,
}: {
  label: string;
  unit: string;
  value: number | null | undefined;
  subtitle?: string;
  icon: React.ReactNode;
  digits: number;
}) {
  // Aligned with the StatCard above on the page : cyber-card surface,
  // accent-red label icon, glowing big number.
  return (
    <div className="cyber-card p-4">
      <div className="cyber-label mb-3 flex items-center gap-2 !text-[10px]">
        <span className="text-[color:var(--color-cyber-accent)]">{icon}</span>
        <span>{label}</span>
      </div>
      <div className="cyber-glow tabular-nums font-mono text-2xl font-extrabold">
        {value != null ? value.toFixed(digits) : "—"}
        <span className="ml-1.5 text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)]">
          {unit}
        </span>
      </div>
      {subtitle && (
        <div className="mt-1 text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-dim)]">
          {subtitle}
        </div>
      )}
    </div>
  );
}
