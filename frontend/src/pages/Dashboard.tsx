import { useQuery } from "@tanstack/react-query";
import {
  Cpu,
  Globe,
  HardDrive,
  Network,
  Router,
  Thermometer,
  Users,
  Zap,
} from "lucide-react";

import { getSlateStatus } from "@/api/slate";
import type { SlateStatus } from "@/types/slate";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import NetworkHubMap from "@/components/NetworkHubMap";
import SpeedtestCard from "@/components/SpeedtestCard";

type Translator = (key: string, params?: Record<string, string | number>) => string;

function formatUptime(t: Translator, seconds: number | null): string {
  if (seconds == null) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return t("dashboard.uptime_days", { d, h, m });
  if (h > 0) return t("dashboard.uptime_hours", { h, m });
  return t("dashboard.uptime_minutes", { m });
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  const gb = bytes / 1024 ** 3;
  return `${gb.toFixed(2)} Go`;
}

function StatCard(props: {
  label: string;
  value: string;
  icon: React.ComponentType<{ className?: string }>;
  hint?: string;
}) {
  const Icon = props.icon;
  return (
    <div className="cyber-card p-4">
      <div className="cyber-label mb-3 flex items-center gap-2">
        <Icon className="cyber-glow h-3 w-3" />
        {props.label}
      </div>
      <div className="cyber-glow font-mono text-2xl tabular-nums font-extrabold">
        {props.value}
      </div>
      {props.hint && (
        <div className="mt-1 text-[10px] uppercase tracking-wider text-[color:var(--color-cyber-muted)]">
          {props.hint}
        </div>
      )}
    </div>
  );
}

function ServiceChip({ name, enabled }: { name: string; enabled: boolean }) {
  return (
    <span className={cn("cyber-chip", enabled && "cyber-chip-on")}>{name}</span>
  );
}

export default function Dashboard() {
  const t = useT();
  const { data, isLoading, isError, error, refetch, isFetching } =
    useQuery<SlateStatus>({
      queryKey: ["slate-status"],
      queryFn: getSlateStatus,
      refetchInterval: 10_000,
    });

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8 flex items-end justify-between">
        <div>
          <div className="cyber-label mb-2 flex items-center gap-2">
            <Zap className="cyber-glow h-3 w-3" />
            {t("dashboard.subtitle")}
          </div>
          <h1
            className="cyber-display cyber-glitch text-4xl"
            data-text={t("dashboard.title").toUpperCase()}
          >
            {t("dashboard.title").toUpperCase()}
          </h1>
        </div>
        <button
          type="button"
          onClick={() => refetch()}
          disabled={isFetching}
          className="cyber-button-ghost px-4 py-2 text-[10px]"
        >
          {isFetching ? t("dashboard.syncing") : `${t("dashboard.refresh")} ▸`}
        </button>
      </header>

      {isLoading && (
        <p className="cyber-label cyber-cursor">{t("dashboard.connecting")}</p>
      )}

      {isError && (
        <div className="cyber-card cyber-card-accent p-4 text-sm text-[color:var(--color-cyber-accent)]">
          <strong className="uppercase tracking-wider">[ {t("common.error")} ] </strong>
          {t("dashboard.error_unreachable", {
            error: error instanceof Error ? error.message : "",
          })}
        </div>
      )}

      {data && (
        <>
          <section className="cyber-card cyber-card-accent mb-6 p-5">
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
              <Router className="cyber-glow h-5 w-5" />
              <h2 className="cyber-display cyber-glow text-xl">
                {data.hostname ?? data.model ?? "Slate"}
              </h2>
              <span className="text-xs uppercase tracking-wider text-[color:var(--color-cyber-muted)]">
                {data.model} · fw {data.firmware_version}
              </span>
              <span
                className={cn(
                  "cyber-chip ml-auto flex items-center gap-2",
                  data.connected ? "cyber-chip-ok" : "cyber-chip-on",
                )}
              >
                <span
                  className={cn(
                    "inline-block h-1.5 w-1.5 rounded-full",
                    data.connected
                      ? "cyber-pulse bg-[color:var(--color-cyber-ok)]"
                      : "bg-[color:var(--color-cyber-accent)]",
                  )}
                />
                {data.connected
                  ? t("dashboard.status_online")
                  : t("dashboard.status_offline")}
              </span>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-xs md:grid-cols-4">
              {([
                ["dashboard.label_lan", data.lan_ip],
                ["dashboard.label_mac", data.mac],
                [
                  "dashboard.label_wan",
                  data.wan_online == null
                    ? "—"
                    : data.wan_online
                      ? t("dashboard.wan_online")
                      : t("dashboard.wan_offline"),
                ],
                ["dashboard.label_country", data.country_code],
              ] as const).map(([labelKey, value]) => (
                <div key={labelKey} className="flex flex-col gap-1">
                  <span className="text-[10px] uppercase tracking-[0.25em] text-[color:var(--color-cyber-muted)]">
                    {t(labelKey)}
                  </span>
                  <span className="cyber-glow-soft font-mono text-sm">
                    {value ?? "—"}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-5">
            <StatCard
              label={t("dashboard.stat_uptime")}
              value={formatUptime(t, data.uptime_seconds)}
              icon={Router}
            />
            <StatCard
              label={t("dashboard.stat_clients")}
              value={data.connected_clients?.toString() ?? "—"}
              icon={Users}
            />
            <StatCard
              label={t("dashboard.stat_cpu_temp")}
              value={
                data.cpu_temperature_celsius != null
                  ? `${data.cpu_temperature_celsius}°C`
                  : "—"
              }
              icon={Thermometer}
              hint={
                data.cpu_count != null
                  ? t("dashboard.stat_cpu_cores", { n: data.cpu_count })
                  : undefined
              }
            />
            <StatCard
              label={t("dashboard.stat_load_1m")}
              value={
                data.load_average_1m != null
                  ? data.load_average_1m.toFixed(2)
                  : "—"
              }
              icon={Cpu}
              hint={
                data.load_average_5m != null
                  ? t("dashboard.stat_load_hint", {
                      l5: data.load_average_5m.toFixed(2),
                      l15: data.load_average_15m?.toFixed(2) ?? "—",
                    })
                  : undefined
              }
            />
            <StatCard
              label={t("dashboard.stat_ram")}
              value={
                data.memory_usage_percent != null
                  ? `${data.memory_usage_percent.toFixed(1)}%`
                  : "—"
              }
              icon={HardDrive}
              hint={t("dashboard.stat_ram_hint", {
                value: formatBytes(data.memory_free_bytes),
              })}
            />
          </section>

          {data.services && Object.keys(data.services).length > 0 && (
            <section className="cyber-card p-5">
              <h3 className="cyber-label mb-3 flex items-center gap-2">
                <Network className="cyber-glow h-3 w-3" />
                {t("dashboard.services_title")}
              </h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(data.services).map(([name, enabled]) => (
                  <ServiceChip key={name} name={name} enabled={enabled} />
                ))}
              </div>
            </section>
          )}

          {/* Vue topologique : Slate au centre, satellites WAN / Tor /
              Tailscale / Réseaux / Radios. Chaque sous-système polle
              indépendamment, l'affichage se met à jour en temps réel. */}
          <div className="mt-6">
            <NetworkHubMap />
          </div>

          {/* Test de débit Cloudflare exécuté DEPUIS le Slate (ping +
              curl download/upload). Spinner pendant ~25 s, puis 3
              tuiles de métriques. */}
          <SpeedtestCard />

          <footer className="mt-6 flex items-center justify-end gap-2 text-[10px] uppercase tracking-[0.25em] text-[color:var(--color-cyber-muted)]">
            <Globe className="h-3 w-3" />
            {t("dashboard.snapshot", {
              time: new Date(data.timestamp).toLocaleTimeString(),
            })}
          </footer>
        </>
      )}
    </div>
  );
}
