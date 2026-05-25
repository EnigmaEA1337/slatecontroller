import { useState } from "react";
import { Eye, ShieldCheck } from "lucide-react";
import type { ProfileScores, ScoreItem } from "@/types/profile";
import { cn } from "@/lib/utils";

function colorFor(percent: number): { bar: string; text: string } {
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

function ScoreBar({
  label,
  percent,
  icon: Icon,
  breakdown,
  expanded,
  onToggle,
}: {
  label: string;
  percent: number;
  icon: typeof ShieldCheck;
  breakdown: ScoreItem[];
  expanded: boolean;
  onToggle: () => void;
}) {
  const color = colorFor(percent);
  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2 text-left"
        title="Click pour voir le breakdown"
      >
        <Icon className={cn("h-3 w-3", color.text)} />
        <span className="cyber-label text-[10px]">{label}</span>
        <span className={cn("ml-auto font-mono text-xs font-bold", color.text)}>
          {percent}%
        </span>
      </button>
      <div className="h-1.5 w-full overflow-hidden border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg)]">
        <div
          className={cn("h-full transition-all", color.bar)}
          style={{ width: `${percent}%` }}
        />
      </div>
      {expanded && (
        <ul className="mt-2 space-y-1 border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-bg)]/50 p-2 text-[10px]">
          {breakdown.map((item) => {
            const got = item.points > 0;
            return (
              <li key={item.name} className="flex items-start gap-2">
                <span
                  className={cn(
                    "mt-0.5 inline-block w-4 text-center font-bold",
                    got ? "text-[color:var(--color-cyber-ok)]" : "text-[color:var(--color-cyber-dim)]",
                  )}
                >
                  {got ? "✓" : "·"}
                </span>
                <span className="flex-1">
                  <span
                    className={cn(
                      got
                        ? "text-[color:var(--color-cyber-fg)]"
                        : "text-[color:var(--color-cyber-dim)]",
                    )}
                  >
                    {item.name}
                  </span>
                  {item.note && (
                    <span className="ml-2 italic text-[color:var(--color-cyber-muted)]">
                      {item.note}
                    </span>
                  )}
                </span>
                <span className="shrink-0 font-mono text-[color:var(--color-cyber-muted)]">
                  {item.points}/{item.max_points}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default function ProfileScoresBars({ scores }: { scores: ProfileScores }) {
  const [expanded, setExpanded] = useState<"anon" | "sec" | null>(null);
  return (
    <div className="space-y-3">
      <ScoreBar
        label="Anonymisation surf"
        percent={scores.anonymization}
        icon={Eye}
        breakdown={scores.breakdown_anonymization}
        expanded={expanded === "anon"}
        onToggle={() => setExpanded(expanded === "anon" ? null : "anon")}
      />
      <ScoreBar
        label="Sécurité"
        percent={scores.security}
        icon={ShieldCheck}
        breakdown={scores.breakdown_security}
        expanded={expanded === "sec"}
        onToggle={() => setExpanded(expanded === "sec" ? null : "sec")}
      />
    </div>
  );
}
