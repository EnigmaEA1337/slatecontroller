/**
 * Chart components for the Security page. Built on Recharts and styled to
 * match the cyberpunk HUD theme (no axes when not needed, sparse legends,
 * cyber color palette).
 *
 * All charts are pure functions of their props — they don't fetch data.
 * That keeps them easy to test and lets the parent pages compose what they
 * need without coupling to the API client.
 */

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Finding, RiskScore } from "@/types/security";
import type { RiskScoreHistoryPoint } from "@/api/security";

// HUD-friendly palette aligned with severity / maturity color usage elsewhere.
const SEVERITY_COLORS: Record<string, string> = {
  critical: "#f87171",   // red-400
  high: "#fb923c",       // orange-400
  medium: "#fde047",     // yellow-300
  low: "#7dd3fc",        // sky-300
  unknown: "#6b7280",    // gray-500
};

const AV_COLORS: Record<string, string> = {
  network: "#f87171",
  adjacent: "#fb923c",
  local: "#fde047",
  physical: "#7dd3fc",
  unknown: "#6b7280",
};

const MATURITY_COLORS: Record<string, string> = {
  in_the_wild: "#f87171",
  weaponized: "#fb923c",
  functional: "#fde047",
  poc: "#7dd3fc",
  none: "#6b7280",
};

const TOOLTIP_BG = "#0c0f14";
const TOOLTIP_BORDER = "rgba(255,255,255,0.18)";

const tooltipProps = {
  contentStyle: {
    background: TOOLTIP_BG,
    border: `1px solid ${TOOLTIP_BORDER}`,
    fontSize: 11,
    color: "#e5e7eb",
  },
  itemStyle: { color: "#e5e7eb" },
  cursor: { fill: "rgba(255,255,255,0.05)" },
};

// ---------------------------- Severity donut ---------------------------- #

export function SeverityDonut({ findings }: { findings: Finding[] }) {
  const data = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const f of findings) counts[f.severity] = (counts[f.severity] ?? 0) + 1;
    return ["critical", "high", "medium", "low", "unknown"]
      .map((k) => ({ name: k, value: counts[k] ?? 0 }))
      .filter((d) => d.value > 0);
  }, [findings]);

  if (data.length === 0) return <EmptyHint label="Pas de données" />;
  return (
    <ChartFrame title="Sévérité">
      <ResponsiveContainer width="100%" height={150}>
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius={40}
            outerRadius={60}
            strokeWidth={1}
            stroke={TOOLTIP_BG}
          >
            {data.map((d) => (
              <Cell key={d.name} fill={SEVERITY_COLORS[d.name]} />
            ))}
          </Pie>
          <Tooltip {...tooltipProps} />
        </PieChart>
      </ResponsiveContainer>
      <DonutLegend
        items={data.map((d) => ({ label: d.name, value: d.value, color: SEVERITY_COLORS[d.name] }))}
      />
    </ChartFrame>
  );
}

// ---------------------------- Attack vector donut ---------------------------- #

export function AttackVectorDonut({ findings }: { findings: Finding[] }) {
  const data = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const f of findings) counts[f.attack_vector] = (counts[f.attack_vector] ?? 0) + 1;
    return ["network", "adjacent", "local", "physical", "unknown"]
      .map((k) => ({ name: k, value: counts[k] ?? 0 }))
      .filter((d) => d.value > 0);
  }, [findings]);

  if (data.length === 0) return <EmptyHint label="Pas de données" />;
  return (
    <ChartFrame title="Attack vector">
      <ResponsiveContainer width="100%" height={150}>
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius={40}
            outerRadius={60}
            strokeWidth={1}
            stroke={TOOLTIP_BG}
          >
            {data.map((d) => (
              <Cell key={d.name} fill={AV_COLORS[d.name]} />
            ))}
          </Pie>
          <Tooltip {...tooltipProps} />
        </PieChart>
      </ResponsiveContainer>
      <DonutLegend
        items={data.map((d) => ({ label: d.name, value: d.value, color: AV_COLORS[d.name] }))}
      />
    </ChartFrame>
  );
}

// ---------------------------- Exploit maturity bar ---------------------------- #

export function ExploitMaturityBar({ findings }: { findings: Finding[] }) {
  const data = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const f of findings) {
      const m = f.exploit?.exploit_maturity ?? "none";
      counts[m] = (counts[m] ?? 0) + 1;
    }
    return ["in_the_wild", "weaponized", "functional", "poc", "none"]
      .map((k) => ({ name: k, value: counts[k] ?? 0 }))
      .filter((d) => d.value > 0);
  }, [findings]);

  if (data.length === 0) return <EmptyHint label="Pas de données" />;
  return (
    <ChartFrame title="Maturité d'exploit">
      <ResponsiveContainer width="100%" height={170}>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 8 }}>
          <XAxis
            type="number"
            stroke="#9ca3af"
            fontSize={9}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            dataKey="name"
            type="category"
            stroke="#9ca3af"
            fontSize={9}
            tickLine={false}
            axisLine={false}
            width={70}
          />
          <Tooltip {...tooltipProps} />
          <Bar dataKey="value" radius={[0, 0, 0, 0]}>
            {data.map((d) => (
              <Cell key={d.name} fill={MATURITY_COLORS[d.name]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------- Top packages bar ---------------------------- #

export function TopPackagesBar({
  findings,
  top = 10,
}: {
  findings: Finding[];
  top?: number;
}) {
  const data = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const f of findings) {
      counts[f.package_name] = (counts[f.package_name] ?? 0) + 1;
    }
    return Object.entries(counts)
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, top);
  }, [findings, top]);

  if (data.length === 0) return <EmptyHint label="Aucun paquet" />;
  return (
    <ChartFrame title={`Top ${top} paquets par CVE`}>
      <ResponsiveContainer width="100%" height={Math.max(180, data.length * 20)}>
        <BarChart data={data} layout="vertical" margin={{ left: 12, right: 16 }}>
          <XAxis
            type="number"
            stroke="#9ca3af"
            fontSize={9}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            dataKey="name"
            type="category"
            stroke="#9ca3af"
            fontSize={9}
            tickLine={false}
            axisLine={false}
            width={120}
          />
          <Tooltip {...tooltipProps} />
          <Bar dataKey="value" fill="#22d3ee" radius={[0, 0, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------- Risk score trend ---------------------------- #

export function RiskScoreTrend({
  points,
}: {
  points: RiskScoreHistoryPoint[];
}) {
  const data = useMemo(
    () =>
      points.map((p) => ({
        ts: new Date(p.taken_at).getTime(),
        label: new Date(p.taken_at).toLocaleString("fr-FR", {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        }),
        score: p.score,
        kev: p.kev_count,
        weaponized: p.weaponized_count,
        remote: p.remote_critical,
      })),
    [points],
  );
  if (data.length === 0) return <EmptyHint label="Pas d'historique" />;
  return (
    <ChartFrame title="Risk Score sur les snapshots">
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ left: 0, right: 10, top: 8, bottom: 8 }}>
          <CartesianGrid stroke="rgba(255,255,255,0.05)" />
          <XAxis
            dataKey="label"
            stroke="#9ca3af"
            fontSize={9}
            tickLine={false}
          />
          <YAxis
            domain={[0, 100]}
            stroke="#9ca3af"
            fontSize={9}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip {...tooltipProps} />
          <Line
            type="monotone"
            dataKey="score"
            stroke="#22d3ee"
            strokeWidth={2}
            dot={{ r: 3, fill: "#22d3ee" }}
            name="Risk Score"
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export function KevWeaponizedTrend({
  points,
}: {
  points: RiskScoreHistoryPoint[];
}) {
  const data = useMemo(
    () =>
      points.map((p) => ({
        ts: new Date(p.taken_at).getTime(),
        label: new Date(p.taken_at).toLocaleString("fr-FR", {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
        }),
        kev: p.kev_count,
        weaponized: p.weaponized_count,
        remote: p.remote_critical,
      })),
    [points],
  );
  if (data.length === 0) return <EmptyHint label="Pas d'historique" />;
  return (
    <ChartFrame title="KEV / Weaponized / Remote critique">
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ left: 0, right: 10, top: 8, bottom: 8 }}>
          <CartesianGrid stroke="rgba(255,255,255,0.05)" />
          <XAxis
            dataKey="label"
            stroke="#9ca3af"
            fontSize={9}
            tickLine={false}
          />
          <YAxis stroke="#9ca3af" fontSize={9} tickLine={false} axisLine={false} />
          <Tooltip {...tooltipProps} />
          <Area
            type="monotone"
            dataKey="kev"
            stroke="#f87171"
            fill="#f87171"
            fillOpacity={0.3}
            name="KEV"
          />
          <Area
            type="monotone"
            dataKey="weaponized"
            stroke="#fb923c"
            fill="#fb923c"
            fillOpacity={0.3}
            name="Weaponized"
          />
          <Area
            type="monotone"
            dataKey="remote"
            stroke="#fde047"
            fill="#fde047"
            fillOpacity={0.2}
            name="Remote critical"
          />
        </AreaChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------- Sparkline (mini line) ---------------------------- #

export function Sparkline({
  values,
  color = "#22d3ee",
  height = 24,
  width = 80,
}: {
  values: number[];
  color?: string;
  height?: number;
  width?: number;
}) {
  if (values.length < 2) return <span className="text-[9px] text-gray-500">–</span>;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const step = width / (values.length - 1);
  const points = values
    .map((v, i) => {
      const x = i * step;
      const y = height - ((v - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
    >
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        points={points}
      />
    </svg>
  );
}

// ---------------------------- ATT&CK tactic coverage ---------------------------- #

export function TacticCoverageBar({
  tactics,
}: {
  tactics: { name: string; coverage: number; touched: number; total: number }[];
}) {
  if (tactics.length === 0) return <EmptyHint label="Pas de techniques touchées" />;
  return (
    <ChartFrame title="Couverture ATT&CK par tactic (% techniques touchées)">
      <ResponsiveContainer width="100%" height={250}>
        <BarChart data={tactics} layout="vertical" margin={{ left: 12, right: 20 }}>
          <XAxis
            type="number"
            domain={[0, 100]}
            stroke="#9ca3af"
            fontSize={9}
            tickLine={false}
            axisLine={false}
            unit="%"
          />
          <YAxis
            dataKey="name"
            type="category"
            stroke="#9ca3af"
            fontSize={9}
            tickLine={false}
            axisLine={false}
            width={150}
          />
          <Tooltip
            {...tooltipProps}
            formatter={(_value: number, _name: string, p: { payload?: { touched?: number; total?: number; coverage?: number } }) =>
              p.payload
                ? [`${p.payload.touched ?? 0} / ${p.payload.total ?? 0} (${(p.payload.coverage ?? 0).toFixed(0)}%)`, "Coverage"]
                : ""
            }
          />
          <Bar dataKey="coverage" radius={[0, 0, 0, 0]}>
            {tactics.map((t) => (
              <Cell
                key={t.name}
                fill={
                  t.coverage >= 50
                    ? "#f87171"
                    : t.coverage >= 20
                    ? "#fb923c"
                    : t.coverage > 0
                    ? "#fde047"
                    : "#6b7280"
                }
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------- Risk acceptance donut ---------------------------- #

export function RiskAcceptanceDonut({ rs }: { rs: RiskScore }) {
  const data = [
    { name: "permanent", value: rs.risk_accepted_unlimited, color: "#fb923c" },
    { name: "avec expiry", value: rs.risk_accepted_limited, color: "#fde047" },
  ].filter((d) => d.value > 0);
  if (data.length === 0) return null;
  return (
    <ChartFrame title={`Risques acceptés (${rs.risk_accepted_count})`}>
      <ResponsiveContainer width="100%" height={130}>
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius={32}
            outerRadius={50}
            strokeWidth={1}
            stroke={TOOLTIP_BG}
          >
            {data.map((d) => (
              <Cell key={d.name} fill={d.color} />
            ))}
          </Pie>
          <Tooltip {...tooltipProps} />
        </PieChart>
      </ResponsiveContainer>
      <DonutLegend items={data.map((d) => ({ label: d.name, value: d.value, color: d.color }))} />
    </ChartFrame>
  );
}

// ---------------------------- Shared layout pieces ---------------------------- #

function ChartFrame({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="cyber-panel flex h-full flex-col p-3">
      <div className="cyber-label mb-2 text-[10px]">{title}</div>
      <div className="flex-1">{children}</div>
    </div>
  );
}

function DonutLegend({
  items,
}: {
  items: { label: string; value: number; color: string }[];
}) {
  return (
    <div className="mt-2 flex flex-wrap gap-2 text-[10px]">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1">
          <span
            className="inline-block h-2 w-2"
            style={{ backgroundColor: it.color }}
          />
          <span className="text-[color:var(--color-cyber-muted)]">{it.label}</span>
          <span className="font-mono text-[color:var(--color-cyber-fg)]">
            {it.value}
          </span>
        </span>
      ))}
    </div>
  );
}

function EmptyHint({ label }: { label: string }) {
  return (
    <div className="cyber-panel flex h-32 items-center justify-center p-3 text-[10px] text-[color:var(--color-cyber-muted)]">
      {label}
    </div>
  );
}
