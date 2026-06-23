// "Reconnaissance WAN" — active discovery sweep page.
//
// Top-level layout :
//   - header with "Lancer un scan" button
//   - active scan card (when one is in flight) with progress bar + cancel
//   - history list (collapsed rows, click to expand → host table)
//   - launch modal : pick interfaces + scan phases
//
// The launch modal queries /api/recon/interfaces live so the operator
// sees what's actually on the Slate (WAN uplink + bridges) and which
// subnets are too wide to scan (the modal disables those checkboxes).
//
// The detail view, once a scan is done, lists hosts in a grouped table
// (one section per interface), each row optionally expandable to its
// open ports + banners.

import { Fragment, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  Play,
  Radar,
  Trash2,
  X,
  XCircle,
  Zap,
} from "lucide-react";

import {
  type ReconHost,
  type ReconInterface,
  type ReconPort,
  type ReconScanSummary,
  type ReconStatus,
  type ReconToolStatus,
  cancelReconScan,
  deleteReconScan,
  getReconScan,
  getReconTools,
  installReconTools,
  launchReconScan,
  listReconInterfaces,
  listReconScans,
} from "@/api/recon";
import { usePinConfirm } from "@/hooks/usePinConfirm";
import { cn } from "@/lib/utils";

const FAMILY_COLORS: Record<ReconInterface["family"], string> = {
  wan: "#fbbf24",
  lan: "var(--color-cyber-accent)",
  guest: "#22d3ee",
  other: "var(--color-cyber-muted)",
};

const STATUS_LABELS: Record<ReconStatus, string> = {
  running: "en cours",
  done: "terminé",
  failed: "échec",
  cancelled: "annulé",
};

export default function WanReconPage() {
  const qc = useQueryClient();
  const [launchOpen, setLaunchOpen] = useState(false);
  const [expandedScanId, setExpandedScanId] = useState<number | null>(null);

  const scans = useQuery({
    queryKey: ["recon", "scans"],
    queryFn: () => listReconScans(),
    // Poll faster while a scan is running. Defensive : guard the
    // type because TanStack Query can briefly hand us an error
    // response body (object) instead of the array if the request
    // 5xxed and was retried.
    refetchInterval: (q) => {
      const data = q.state.data;
      if (!Array.isArray(data)) return 30_000;
      return data.some((s) => s.status === "running") ? 1500 : 30_000;
    },
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteReconScan(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recon"] }),
  });
  const cancelMut = useMutation({
    mutationFn: (id: number) => cancelReconScan(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recon"] }),
  });

  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);
  const pinGate = usePinConfirm({
    title: "Supprimer le scan",
    description:
      pendingDeleteId !== null
        ? `Supprimer le scan #${pendingDeleteId} et tous ses hôtes/ports découverts ? Action irréversible.`
        : undefined,
    onConfirmed: () => {
      if (pendingDeleteId !== null) {
        deleteMut.mutate(pendingDeleteId);
        setPendingDeleteId(null);
      }
    },
  });
  const requestDelete = (id: number) => {
    setPendingDeleteId(id);
    pinGate.request();
  };

  const scansList = useMemo<ReconScanSummary[]>(
    () => (Array.isArray(scans.data) ? scans.data : []),
    [scans.data],
  );
  const activeScans = useMemo(
    () => scansList.filter((s) => s.status === "running"),
    [scansList],
  );
  const pastScans = useMemo(
    () => scansList.filter((s) => s.status !== "running"),
    [scansList],
  );

  return (
    <div className="space-y-6 p-6">
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <Radar className="cyber-glow h-5 w-5" />
            <h1 className="cyber-display cyber-glow text-2xl">
              RECONNAISSANCE WAN
            </h1>
          </div>
          <button
            onClick={() => setLaunchOpen(true)}
            className="cyber-button-primary px-3 py-1 text-xs flex items-center gap-2"
          >
            <Play className="h-3 w-3" /> Lancer un scan
          </button>
        </div>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          ARP · ping sweep · TCP probe · bannières — depuis l'angle du Slate
        </p>
      </div>

      {/* Active scans — emphasised, live progress. */}
      {activeScans.length > 0 && (
        <section className="cyber-card p-3 space-y-2">
          <header className="cyber-label text-[10px] flex items-center gap-2">
            <Loader2 className="h-3 w-3 animate-spin" />
            EN COURS ({activeScans.length})
          </header>
          {activeScans.map((s) => (
            <ScanRow
              key={s.id}
              scan={s}
              expanded={false}
              onExpand={() => {}}
              onCancel={() => cancelMut.mutate(s.id)}
              onDelete={() => requestDelete(s.id)}
              detailQueryEnabled={false}
            />
          ))}
        </section>
      )}

      <section className="cyber-card p-3 space-y-2">
        <header className="cyber-label text-[10px]">
          ARCHIVE ({pastScans.length})
        </header>
        {pastScans.length === 0 && (
          <p className="text-xs text-[color:var(--color-cyber-muted)]">
            Aucun scan archivé. Lance un premier sweep pour voir qui est sur le réseau.
          </p>
        )}
        {pastScans.map((s) => (
          <ScanRow
            key={s.id}
            scan={s}
            expanded={expandedScanId === s.id}
            onExpand={() => setExpandedScanId((cur) => (cur === s.id ? null : s.id))}
            onCancel={() => cancelMut.mutate(s.id)}
            onDelete={() => requestDelete(s.id)}
            detailQueryEnabled={expandedScanId === s.id}
          />
        ))}
      </section>

      <LaunchScanModal
        open={launchOpen}
        onClose={() => setLaunchOpen(false)}
        onLaunched={() => {
          setLaunchOpen(false);
          qc.invalidateQueries({ queryKey: ["recon", "scans"] });
        }}
      />
      {pinGate.modal}
    </div>
  );
}


// ---------------------------- Scan row ---------------------------- //

interface ScanRowProps {
  scan: ReconScanSummary;
  expanded: boolean;
  onExpand: () => void;
  onCancel: () => void;
  onDelete: () => void;
  detailQueryEnabled: boolean;
}

function ScanRow({
  scan,
  expanded,
  onExpand,
  onCancel,
  onDelete,
  detailQueryEnabled,
}: ScanRowProps) {
  const startedAt = new Date(scan.started_at);
  const finishedAt = scan.finished_at ? new Date(scan.finished_at) : null;
  const durationS = finishedAt
    ? Math.round((finishedAt.getTime() - startedAt.getTime()) / 1000)
    : Math.round((Date.now() - startedAt.getTime()) / 1000);

  return (
    <div className="border border-[color:var(--color-cyber-border)]/40 rounded-sm">
      <div className="flex items-center gap-2 p-2 text-xs">
        {scan.status !== "running" && (
          <button
            onClick={onExpand}
            className="shrink-0 text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-accent)]"
            title={expanded ? "Replier" : "Voir hôtes + ports"}
          >
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          </button>
        )}
        <StatusBadge status={scan.status} />
        <div className="flex-1 min-w-0">
          <div className="font-mono text-xs truncate">
            #{scan.id} ·{" "}
            <span className="text-[color:var(--color-cyber-accent)]">
              {(scan.scope as { interfaces?: string[] }).interfaces?.join(", ") ?? "?"}
            </span>
          </div>
          <div className="text-[10px] text-[color:var(--color-cyber-muted)] mt-0.5 flex items-center gap-2 flex-wrap">
            <span>{startedAt.toLocaleString("fr-FR")}</span>
            <span>·</span>
            <span>{durationS}s</span>
            <span>·</span>
            <span>{scan.host_count} hôtes</span>
            <span>·</span>
            <span>{scan.port_count} ports</span>
            {scan.status === "running" && scan.progress && (
              <>
                <span>·</span>
                <span className="text-[color:var(--color-cyber-accent)]">{scan.progress}</span>
              </>
            )}
            {scan.status === "failed" && scan.error && (
              <>
                <span>·</span>
                <span className="text-rose-300">{scan.error}</span>
              </>
            )}
          </div>
        </div>
        {scan.status === "running" && (
          <button
            onClick={onCancel}
            className="cyber-button-ghost px-2 py-1 text-[10px] shrink-0"
            title="Annuler le scan"
          >
            stop
          </button>
        )}
        <button
          onClick={onDelete}
          className="cyber-button-ghost p-1 shrink-0 text-[color:var(--color-cyber-muted)] hover:text-amber-300"
          title="Supprimer ce scan"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
      {expanded && scan.status !== "running" && detailQueryEnabled && (
        <ScanDetail scanId={scan.id} />
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: ReconStatus }) {
  const color =
    status === "done"
      ? "text-emerald-300"
      : status === "running"
        ? "text-cyan-300"
        : status === "failed"
          ? "text-rose-300"
          : "text-[color:var(--color-cyber-muted)]";
  const Icon =
    status === "done"
      ? CheckCircle2
      : status === "running"
        ? Loader2
        : status === "failed"
          ? XCircle
          : AlertTriangle;
  return (
    <span
      className={cn(
        "shrink-0 flex items-center gap-1 text-[10px] font-mono uppercase",
        color,
      )}
    >
      <Icon className={cn("h-3 w-3", status === "running" && "animate-spin")} />
      {STATUS_LABELS[status]}
    </span>
  );
}


// ---------------------------- Scan detail ---------------------------- //

function ScanDetail({ scanId }: { scanId: number }) {
  const detail = useQuery({
    queryKey: ["recon", "scans", scanId],
    queryFn: () => getReconScan(scanId),
  });

  if (!detail.data) {
    return (
      <div className="p-3 text-[10px] text-[color:var(--color-cyber-muted)]">
        Chargement…
      </div>
    );
  }

  const portsByIp = new Map<string, ReconPort[]>();
  for (const p of detail.data.ports) {
    if (!portsByIp.has(p.ip)) portsByIp.set(p.ip, []);
    portsByIp.get(p.ip)!.push(p);
  }
  const hostsByInterface = new Map<string, ReconHost[]>();
  for (const h of detail.data.hosts) {
    if (!hostsByInterface.has(h.interface)) hostsByInterface.set(h.interface, []);
    hostsByInterface.get(h.interface)!.push(h);
  }

  return (
    <div className="px-3 pb-3 space-y-3">
      {[...hostsByInterface.entries()].map(([iface, hosts]) => (
        <div key={iface} className="space-y-1">
          <div className="text-[10px] uppercase font-mono text-[color:var(--color-cyber-muted)]">
            {iface} — {hosts.length} hôtes
          </div>
          <HostTable hosts={hosts} portsByIp={portsByIp} />
        </div>
      ))}
      {detail.data.hosts.length === 0 && (
        <p className="text-[10px] text-[color:var(--color-cyber-muted)]">
          Aucun hôte découvert.
        </p>
      )}
    </div>
  );
}

function HostTable({
  hosts,
  portsByIp,
}: {
  hosts: ReconHost[];
  portsByIp: Map<string, ReconPort[]>;
}) {
  const [openIp, setOpenIp] = useState<string | null>(null);
  return (
    <table className="w-full font-mono text-[11px]">
      <thead>
        <tr className="text-[color:var(--color-cyber-muted)] text-left">
          <th className="px-2 py-1 w-8"></th>
          <th className="px-2 py-1">IP</th>
          <th className="px-2 py-1">MAC</th>
          <th className="px-2 py-1">vendor</th>
          <th className="px-2 py-1">hostname</th>
          <th className="px-2 py-1">source</th>
          <th className="px-2 py-1">ports</th>
        </tr>
      </thead>
      <tbody>
        {hosts.map((h) => {
          const ports = portsByIp.get(h.ip) ?? [];
          const isOpen = openIp === h.ip;
          return (
            <Fragment key={h.ip}>
              <tr
                className="border-t border-[color:var(--color-cyber-border)]/30"
              >
                <td className="px-2 py-0.5">
                  {ports.length > 0 && (
                    <button
                      onClick={() =>
                        setOpenIp((cur) => (cur === h.ip ? null : h.ip))
                      }
                      className="text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-accent)]"
                    >
                      {isOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                    </button>
                  )}
                </td>
                <td className="px-2 py-0.5">
                  <span
                    className={cn(
                      h.is_gateway && "text-amber-300",
                      h.is_self && "text-cyan-300",
                    )}
                  >
                    {h.ip}
                    {h.is_gateway && " · gw"}
                    {h.is_self && " · slate"}
                  </span>
                </td>
                <td className="px-2 py-0.5 text-[10px]">{h.mac || "—"}</td>
                <td className="px-2 py-0.5 text-[10px]">{h.vendor || "—"}</td>
                <td className="px-2 py-0.5 text-[10px]">{h.hostname || "—"}</td>
                <td className="px-2 py-0.5 text-[10px] text-[color:var(--color-cyber-muted)]">
                  {h.source}
                </td>
                <td className="px-2 py-0.5 text-[10px]">
                  {ports.length === 0 ? (
                    <span className="text-[color:var(--color-cyber-muted)]">—</span>
                  ) : (
                    <span className="text-emerald-300">{ports.length} open</span>
                  )}
                </td>
              </tr>
              {isOpen &&
                ports.map((p) => (
                  <tr key={`${h.ip}:${p.port}`} className="bg-[color:var(--color-cyber-bg-2)]/40">
                    <td></td>
                    <td colSpan={6} className="px-2 py-0.5 text-[10px]">
                      <span className="font-mono text-emerald-300">:{p.port}</span>
                      {p.service && (
                        <span className="ml-2 text-[color:var(--color-cyber-accent)]">{p.service}</span>
                      )}
                      {p.banner && (
                        <span className="ml-2 text-[color:var(--color-cyber-muted)] italic truncate inline-block max-w-[700px] align-bottom">
                          {p.banner}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}


// ---------------------------- Launch modal ---------------------------- //

interface LaunchModalProps {
  open: boolean;
  onClose: () => void;
  onLaunched: () => void;
}

function LaunchScanModal({ open, onClose, onLaunched }: LaunchModalProps) {
  const interfaces = useQuery({
    queryKey: ["recon", "interfaces"],
    queryFn: () => listReconInterfaces(),
    enabled: open,
  });
  const tools = useQuery({
    queryKey: ["recon", "tools"],
    queryFn: () => getReconTools(),
    enabled: open,
  });
  const interfaceList = useMemo<ReconInterface[]>(
    () => (Array.isArray(interfaces.data) ? interfaces.data : []),
    [interfaces.data],
  );

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [doArp, setDoArp] = useState(true);
  const [doPing, setDoPing] = useState(true);
  const [doTcp, setDoTcp] = useState(true);
  const [doBanner, setDoBanner] = useState(true);

  const launchMut = useMutation({
    mutationFn: launchReconScan,
    onSuccess: onLaunched,
  });

  if (!open) return null;

  const toggle = (name: string) => {
    setSelected((cur) => {
      const n = new Set(cur);
      if (n.has(name)) n.delete(name);
      else n.add(name);
      return n;
    });
  };

  const submit = () => {
    launchMut.mutate({
      interfaces: [...selected],
      do_arp: doArp,
      do_ping: doPing,
      do_tcp: doTcp,
      do_banner: doBanner,
    });
  };

  const canSubmit = selected.size > 0 && !launchMut.isPending;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
      <div className="cyber-card p-4 w-full max-w-2xl space-y-4">
        <header className="flex items-center justify-between gap-2">
          <h2 className="cyber-label text-xs flex items-center gap-2">
            <Radar className="h-4 w-4" /> NOUVEAU SCAN
          </h2>
          <button
            onClick={onClose}
            className="cyber-button-ghost p-1"
            title="Fermer"
          >
            <X className="h-3 w-3" />
          </button>
        </header>

        <ToolsPanel status={tools.data} loading={tools.isLoading} onChange={() => tools.refetch()} />

        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
            Interfaces
          </div>
          {interfaces.isLoading && (
            <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
              Chargement…
            </div>
          )}
          {interfaces.isError && (
            <div className="text-[10px] text-rose-300">
              Échec de la liste d'interfaces : {(interfaces.error as Error)?.message ?? "réponse inattendue"}
            </div>
          )}
          {interfaceList.map((i) => (
            <label
              key={i.name}
              className="flex items-center gap-2 p-2 border border-[color:var(--color-cyber-border)]/40 rounded-sm"
            >
              <input
                type="checkbox"
                checked={selected.has(i.name)}
                onChange={() => toggle(i.name)}
              />
              <span
                className="font-mono text-[10px] uppercase shrink-0 px-1.5 py-0.5 rounded"
                style={{
                  color: FAMILY_COLORS[i.family],
                  border: `1px solid ${FAMILY_COLORS[i.family]}66`,
                }}
              >
                {i.family}
              </span>
              <span className="font-mono text-xs flex-1">{i.name}</span>
              <div className="flex flex-col items-end gap-0.5 text-[10px] font-mono text-[color:var(--color-cyber-muted)]">
                <span>{i.ipv4_cidr}</span>
                {i.scan_clamped && (
                  <span
                    className="text-amber-300 text-[9px]"
                    title={`Subnet trop large pour un sweep complet — limité au /24 autour de l'IP du Slate (${i.scan_cidr})`}
                  >
                    sweep clamp → {i.scan_cidr}
                  </span>
                )}
              </div>
            </label>
          ))}
        </div>

        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
            Phases
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={doArp}
                onChange={(e) => setDoArp(e.target.checked)}
              />
              ARP cache
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={doPing}
                onChange={(e) => setDoPing(e.target.checked)}
              />
              Ping sweep
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={doTcp}
                onChange={(e) => setDoTcp(e.target.checked)}
              />
              TCP probe
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={doBanner}
                onChange={(e) => setDoBanner(e.target.checked)}
                disabled={!doTcp}
              />
              Banner grab
            </label>
          </div>
        </div>

        {launchMut.error && (
          <div className="text-[10px] text-rose-300">
            {(launchMut.error as Error).message}
          </div>
        )}

        <footer className="flex items-center justify-end gap-2 pt-2 border-t border-[color:var(--color-cyber-border)]/40">
          <button onClick={onClose} className="cyber-button-ghost px-3 py-1 text-xs">
            annuler
          </button>
          <button
            onClick={submit}
            disabled={!canSubmit}
            className="cyber-button-primary px-3 py-1 text-xs flex items-center gap-2"
          >
            {launchMut.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Play className="h-3 w-3" />
            )}
            Lancer
          </button>
        </footer>
      </div>
    </div>
  );
}


// ---------------------------- Tools panel ---------------------------- //

function ToolsPanel({
  status,
  loading,
  onChange,
}: {
  status: ReconToolStatus | undefined;
  loading: boolean;
  onChange: () => void;
}) {
  const [installing, setInstalling] = useState(false);
  const [installLog, setInstallLog] = useState<string | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);

  const handleInstall = async () => {
    setInstalling(true);
    setInstallLog(null);
    setInstallError(null);
    try {
      const report = await installReconTools();
      setInstallLog(report.log);
      if (!report.ok) {
        setInstallError("Installation incomplète — voir le log");
      }
      onChange();
    } catch (e) {
      setInstallError((e as Error).message);
    } finally {
      setInstalling(false);
    }
  };

  if (loading || !status) {
    return (
      <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
        Détection des outils…
      </div>
    );
  }

  return (
    <div className="space-y-2 p-2 border border-[color:var(--color-cyber-border)]/40 rounded-sm">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] flex items-center gap-2">
          <Zap className="h-3 w-3" />
          Moteur de scan
        </div>
        <div className="text-[10px] font-mono text-[color:var(--color-cyber-muted)]">
          {status.fully_installed ? (
            <span className="text-emerald-300">avancé (nmap + arp-scan)</span>
          ) : (
            <span className="text-amber-300">basique (ping + nc)</span>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-[10px] font-mono">
        <ToolBadge
          name="nmap"
          installed={status.has_nmap}
          version={status.nmap_version}
        />
        <ToolBadge
          name="arp-scan"
          installed={status.has_arp_scan}
          version={status.arp_scan_version}
        />
      </div>
      {!status.fully_installed && (
        <div className="space-y-2">
          <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
            Outils avancés non installés. L'install ajoute ~5-10 MB sur{" "}
            <span className="font-mono">/overlay</span> (libre : {status.overlay_free_mb} MB).
            Apporte : discovery layer-2 (arp-scan voit les hôtes silencieux) +
            détection de version (<span className="font-mono">nmap -sV</span> → bannières propres
            type <span className="font-mono">OpenSSH_9.6</span>).
          </div>
          <button
            onClick={handleInstall}
            disabled={installing}
            className="cyber-button-primary px-3 py-1 text-[10px] flex items-center gap-2"
          >
            {installing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
            {installing ? "Installation en cours…" : "Installer nmap + arp-scan"}
          </button>
        </div>
      )}
      {installError && (
        <div className="text-[10px] text-rose-300">{installError}</div>
      )}
      {installLog && (
        <details className="text-[10px]">
          <summary className="cursor-pointer text-[color:var(--color-cyber-muted)]">
            Log opkg
          </summary>
          <pre className="mt-1 max-h-48 overflow-y-auto text-[9px] font-mono whitespace-pre-wrap text-[color:var(--color-cyber-muted)] bg-[color:var(--color-cyber-bg-2)]/40 p-2 rounded">
            {installLog}
          </pre>
        </details>
      )}
    </div>
  );
}

function ToolBadge({
  name,
  installed,
  version,
}: {
  name: string;
  installed: boolean;
  version: string;
}) {
  return (
    <div className="flex items-center gap-2">
      {installed ? (
        <CheckCircle2 className="h-3 w-3 text-emerald-300 shrink-0" />
      ) : (
        <XCircle className="h-3 w-3 text-[color:var(--color-cyber-muted)] shrink-0" />
      )}
      <span className={cn(installed ? "text-emerald-300" : "text-[color:var(--color-cyber-muted)]")}>
        {name}
      </span>
      {installed && version && (
        <span className="text-[color:var(--color-cyber-muted)] text-[9px]">v{version}</span>
      )}
    </div>
  );
}
