import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleDashed,
  RefreshCw,
  ShieldAlert,
  Sliders,
  XCircle,
} from "lucide-react";
import { getSlateHardening } from "@/api/slate";
import type { HardeningCheck, HardeningResponse } from "@/types/hardening";
import { cn } from "@/lib/utils";

type Verdict = "COMPLIANT" | "PARTIAL" | "NON-COMPLIANT";

function verdictFor(check: HardeningCheck): Verdict {
  if (check.status === "error") return "NON-COMPLIANT";
  if (check.status === "needs_probe" || check.status === "skipped") {
    return "PARTIAL";
  }
  // status === "ready"
  if (check.points >= check.max_points) return "COMPLIANT";
  if (check.points <= 0) return "NON-COMPLIANT";
  return "PARTIAL";
}

const VERDICT_ICON: Record<Verdict, typeof CheckCircle2> = {
  COMPLIANT: CheckCircle2,
  PARTIAL: CircleDashed,
  "NON-COMPLIANT": XCircle,
};

const VERDICT_CHIP: Record<Verdict, string> = {
  COMPLIANT: "cyber-chip-ok",
  PARTIAL: "cyber-chip-warn",
  "NON-COMPLIANT": "cyber-chip-on",
};

const VERDICT_ICON_COLOR: Record<Verdict, string> = {
  COMPLIANT: "text-[color:var(--color-cyber-ok)]",
  PARTIAL: "text-[color:var(--color-cyber-warn)]",
  "NON-COMPLIANT": "text-[color:var(--color-cyber-accent)]",
};

function colorClass(percent: number): { bar: string; text: string } {
  if (percent >= 75) {
    return {
      bar: "bg-[color:var(--color-cyber-ok)]",
      text: "text-[color:var(--color-cyber-ok)]",
    };
  }
  if (percent >= 40) {
    return {
      bar: "bg-[color:var(--color-cyber-warn)]",
      text: "text-[color:var(--color-cyber-warn)]",
    };
  }
  return {
    bar: "bg-[color:var(--color-cyber-accent)]",
    text: "text-[color:var(--color-cyber-accent)]",
  };
}

function CheckRow({ check }: { check: HardeningCheck }) {
  const verdict = verdictFor(check);
  const Icon = VERDICT_ICON[verdict];
  const compliant = verdict === "COMPLIANT";
  return (
    <li className="flex items-start gap-2 border border-[color:var(--color-cyber-border)] p-2.5 text-[11px]">
      <Icon
        className={cn("mt-0.5 h-3 w-3 shrink-0", VERDICT_ICON_COLOR[verdict])}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span
            className={cn(
              compliant
                ? "text-[color:var(--color-cyber-fg)]"
                : "text-[color:var(--color-cyber-muted)]",
            )}
          >
            {check.name}
          </span>
          <span className={cn("cyber-chip", VERDICT_CHIP[verdict])}>
            {verdict}
          </span>
        </div>
        {check.note && (
          <p className="mt-0.5 italic text-[color:var(--color-cyber-dim)]">
            {check.note}
          </p>
        )}
      </div>
      <span className="shrink-0 font-mono text-[color:var(--color-cyber-muted)]">
        {check.points}/{check.max_points}
      </span>
    </li>
  );
}

export default function DeviceHardeningGauge() {
  const [expanded, setExpanded] = useState(false);
  const query = useQuery<HardeningResponse>({
    queryKey: ["slate-hardening"],
    queryFn: getSlateHardening,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  if (query.isLoading) {
    return (
      <section className="cyber-card p-4">
        <p className="cyber-label cyber-cursor text-[10px]">scan hardening</p>
      </section>
    );
  }

  if (query.isError || !query.data) {
    return (
      <section className="cyber-card cyber-card-accent p-4">
        <h3 className="cyber-label flex items-center gap-2">
          <ShieldAlert className="cyber-glow h-3 w-3" />
          device hardening
        </h3>
        <p className="mt-2 text-xs text-[color:var(--color-cyber-accent)]">
          Slate injoignable — gauge non disponible
        </p>
      </section>
    );
  }

  const { percent, score, max_score, checks } = query.data;
  const color = colorClass(percent);
  const verdicts = checks.map(verdictFor);
  const compliantCount = verdicts.filter((v) => v === "COMPLIANT").length;
  const partialCount = verdicts.filter((v) => v === "PARTIAL").length;
  const nonCompliantCount = verdicts.filter((v) => v === "NON-COMPLIANT").length;

  return (
    <section className="cyber-card cyber-card-accent p-5">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-3 text-left"
      >
        <ShieldAlert className={cn("h-5 w-5", color.text)} />
        <div className="flex-1">
          <div className="cyber-label flex items-center gap-2 text-[10px]">
            <span>device hardening</span>
            <span className="text-[color:var(--color-cyber-muted)]">
              {compliantCount} compliant · {partialCount} partial
              {nonCompliantCount > 0 ? ` · ${nonCompliantCount} non-compliant` : ""}
            </span>
          </div>
          <div className="mt-1.5 flex items-baseline gap-3">
            <span className={cn("font-mono text-3xl font-extrabold", color.text)}>
              {percent}%
            </span>
            <span className="font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
              {score}/{max_score} pts
            </span>
          </div>
        </div>
        <Sliders className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />
        {expanded ? (
          <ChevronUp className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />
        ) : (
          <ChevronDown className="h-4 w-4 text-[color:var(--color-cyber-muted)]" />
        )}
      </button>

      <div
        className={cn(
          "mt-3 h-2 w-full overflow-hidden border border-[color:var(--color-cyber-border)]",
        )}
      >
        <div
          className={cn("h-full transition-all", color.bar)}
          style={{ width: `${percent}%` }}
        />
      </div>

      {expanded && (
        <>
          <div className="mt-4 flex items-center justify-between">
            <span className="cyber-label text-[10px]">checks</span>
            <button
              type="button"
              disabled={query.isFetching}
              onClick={(e) => {
                e.stopPropagation();
                void query.refetch();
              }}
              className="inline-flex items-center gap-1.5 border border-transparent px-2 py-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-50"
            >
              <RefreshCw
                className={cn("h-3 w-3", query.isFetching && "animate-spin")}
              />
              {query.isFetching ? "scan…" : "rescan"}
            </button>
          </div>
          <ul className="mt-2 space-y-1.5">
            {checks.map((c, i) => (
              <CheckRow key={i} check={c} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
