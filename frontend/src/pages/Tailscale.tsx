import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  LogOut,
  Network as NetworkIcon,
  Power,
  RefreshCw,
  ShieldCheck,
  Zap,
} from "lucide-react";
import {
  connectTailscale,
  disconnectTailscale,
  getTailscaleConfig,
  getTailscaleStatus,
  logoutTailscale,
  pingTailscale,
  tracerouteTailscale,
} from "@/api/tailscale";
import { listNetworks } from "@/api/networks";
import type {
  TailscaleBackendState,
  TailscaleConfigInput,
} from "@/types/tailscale";
import { ClickableHost } from "@/components/ClickableHost";
import TailscaleHAPanel from "@/components/TailscaleHAPanel";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/lib/error-utils";


const STATE_STYLES: Record<TailscaleBackendState, string> = {
  Running:
    "border-emerald-500/60 bg-emerald-500/10 text-emerald-300 cyber-glow-ok",
  Starting: "border-yellow-500/60 bg-yellow-500/10 text-yellow-200",
  NeedsLogin: "border-orange-500/60 bg-orange-500/10 text-orange-300",
  NeedsMachineAuth: "border-orange-500/60 bg-orange-500/10 text-orange-300",
  Stopped: "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]",
  NoState: "border-[color:var(--color-cyber-border)] text-[color:var(--color-cyber-muted)]",
};

const STATE_LABEL: Record<TailscaleBackendState, string> = {
  Running: "running",
  Starting: "starting",
  NeedsLogin: "needs login",
  NeedsMachineAuth: "machine auth pending",
  Stopped: "stopped",
  NoState: "no state",
};

export default function Tailscale() {
  const qc = useQueryClient();
  const statusQ = useQuery({
    queryKey: ["tailscale", "status"],
    queryFn: getTailscaleStatus,
    refetchInterval: 15_000,
  });
  const configQ = useQuery({
    queryKey: ["tailscale", "config"],
    queryFn: getTailscaleConfig,
  });

  // Connection form state.
  const [authKey, setAuthKey] = useState("");
  const [hostname, setHostname] = useState("");
  const [acceptRoutes, setAcceptRoutes] = useState(true);
  const [acceptDns, setAcceptDns] = useState(false);
  const [advertiseRoutes, setAdvertiseRoutes] = useState(
    "10.137.42.0/24,10.91.18.0/24,10.204.5.0/24,10.66.211.0/24,10.183.7.0/24",
  );
  const [advertiseExitNode, setAdvertiseExitNode] = useState(false);
  const [exitNode, setExitNode] = useState("");
  const [shieldsUp, setShieldsUp] = useState(false);

  // Prime hostname default from device hostname once known.
  useEffect(() => {
    const h = statusQ.data?.hostname;
    if (h && !hostname) setHostname(h);
  }, [statusQ.data?.hostname, hostname]);

  const connectMutation = useMutation({
    mutationFn: () => {
      const body: TailscaleConfigInput = {
        auth_key: authKey.trim() || undefined,
        hostname: hostname.trim() || undefined,
        accept_routes: acceptRoutes,
        accept_dns: acceptDns,
        advertise_routes: advertiseRoutes
          .split(/[,\s]+/)
          .map((s) => s.trim())
          .filter(Boolean),
        advertise_exit_node: advertiseExitNode,
        exit_node: exitNode.trim(),
        shields_up: shieldsUp,
      };
      return connectTailscale(body);
    },
    onSuccess: () => {
      setAuthKey(""); // never keep it in state once accepted
      qc.invalidateQueries({ queryKey: ["tailscale"] });
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: disconnectTailscale,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tailscale"] }),
  });

  const logoutMutation = useMutation({
    mutationFn: logoutTailscale,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tailscale"] }),
  });

  const s = statusQ.data;
  const stateStyle =
    (s && STATE_STYLES[s.backend_state]) || STATE_STYLES.NoState;
  const stateLabel = (s && STATE_LABEL[s.backend_state]) || "?";
  const hasAuthKey = configQ.data?.has_auth_key ?? false;

  // Controller intent : CIDRs the user has marked `expose_to_tailnet` on
  // their Network catalog. This is the SOURCE OF TRUTH for what SHOULD
  // be advertised on the tailnet. The live `advertised_routes` from
  // `tailscale status` may differ (stale state in tailscaled across
  // factory resets, manual `tailscale set --advertise-routes` from
  // someone outside the controller, etc.) ; we surface the drift below.
  const networksQ = useQuery({
    queryKey: ["networks"],
    queryFn: listNetworks,
  });
  const intentedRoutes: string[] = [];
  for (const n of networksQ.data ?? []) {
    if (!n.expose_to_tailnet) continue;
    if (n.subnet_cidr) intentedRoutes.push(n.subnet_cidr);
    if (n.ipv6_enabled && n.ipv6_subnet_cidr) {
      intentedRoutes.push(n.ipv6_subnet_cidr);
    }
  }
  const liveRoutes = s?.advertised_routes ?? [];
  const intentSet = new Set(intentedRoutes);
  const liveSet = new Set(liveRoutes);
  const onlyInLive = liveRoutes.filter((r) => !intentSet.has(r));
  const onlyInIntent = intentedRoutes.filter((r) => !liveSet.has(r));
  const hasDrift = onlyInLive.length > 0 || onlyInIntent.length > 0;

  return (
    <div className="space-y-6 p-6">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <NetworkIcon className="cyber-glow h-5 w-5" />
          <h1 className="cyber-display cyber-glow text-2xl">TAILSCALE</h1>
        </div>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
          Mesh VPN — canal de remote admin + accès LAN home depuis le Slate en
          mobilité.
        </p>
      </div>

      {/* Status */}
      <div className="cyber-panel space-y-3 p-5">
        <div className="flex flex-wrap items-center gap-3">
          <span className="cyber-label text-[10px]">État</span>
          <span
            className={cn(
              "inline-flex items-center border px-2 py-[2px] text-[10px] font-bold uppercase tracking-[0.18em]",
              stateStyle,
            )}
          >
            {stateLabel}
          </span>
          {s?.daemon_running ? (
            <span className="text-[10px] text-[color:var(--color-cyber-muted)]">
              daemon up
            </span>
          ) : s?.installed ? (
            <span className="text-[10px] text-yellow-300">daemon down</span>
          ) : (
            <span className="text-[10px] text-red-300">
              tailscale not installed on Slate
            </span>
          )}
          <button
            type="button"
            onClick={() => statusQ.refetch()}
            disabled={statusQ.isFetching}
            className="ml-auto inline-flex items-center gap-1 border border-[color:var(--color-cyber-border)] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-fg)] disabled:opacity-50"
          >
            <RefreshCw
              className={cn("h-3 w-3", statusQ.isFetching && "animate-spin")}
            />
            {statusQ.isFetching ? "scan…" : "refresh"}
          </button>
        </div>
        {s && (
          <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
            <Info label="Hostname" value={s.hostname || "—"} />
            <Info
              label="Tailnet IP"
              value={
                s.tailscale_ips[0] ? (
                  <ClickableHost value={s.tailscale_ips[0]} />
                ) : (
                  "—"
                )
              }
              accent={!!s.tailscale_ips[0]}
            />
            <Info label="Tailnet" value={s.tailnet || "—"} />
            <Info
              label="Routes acceptées"
              value={s.accept_routes ? "oui" : "non"}
            />
            {/* Intent first (controller catalog), live state second.
                Drift is highlighted explicitly so it stops being a
                silent footgun. */}
            <Info
              label="Routes annoncées (intent)"
              value={
                intentedRoutes.length > 0 ? intentedRoutes.join(", ") : "—"
              }
              accent={intentedRoutes.length > 0}
            />
            {hasDrift && (
              <Info
                label="⚠ Drift live vs intent"
                value={
                  [
                    onlyInLive.length > 0
                      ? `live-only: ${onlyInLive.join(", ")}`
                      : "",
                    onlyInIntent.length > 0
                      ? `intent-only: ${onlyInIntent.join(", ")}`
                      : "",
                  ]
                    .filter(Boolean)
                    .join(" · ")
                }
              />
            )}
            {s.exit_node_enabled && (
              <Info label="Exit node" value="on (this Slate)" accent />
            )}
            {s.use_exit_node && (
              <Info label="Sortie via" value={s.use_exit_node} accent />
            )}
          </div>
        )}
        {s?.error && (
          <div className="cyber-panel border border-red-500/40 bg-red-500/5 p-2 text-[10px] text-red-300">
            <AlertTriangle className="mr-1 inline h-3 w-3" />
            {s.error}
          </div>
        )}
        {s?.backend_state === "Running" && (
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => disconnectMutation.mutate()}
              disabled={disconnectMutation.isPending}
              className="inline-flex items-center gap-1 border border-yellow-500/60 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-yellow-200 hover:bg-yellow-500/10 disabled:opacity-50"
            >
              <Power className="h-3 w-3" />
              Disconnect (down)
            </button>
            <button
              type="button"
              onClick={() => {
                if (
                  confirm(
                    "Logout efface l'identité du Slate sur Tailscale + supprime l'auth key stockée. Confirmer ?",
                  )
                ) {
                  logoutMutation.mutate();
                }
              }}
              disabled={logoutMutation.isPending}
              className="inline-flex items-center gap-1 border border-red-500/60 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-red-300 hover:bg-red-500/10 disabled:opacity-50"
            >
              <LogOut className="h-3 w-3" />
              Logout (wipe identity)
            </button>
          </div>
        )}
      </div>

      {/* Audit sécurité Tailscale — déplacé dans /security/tailscale */}
      <div className="cyber-panel flex items-center gap-3 p-4">
        <ShieldCheck className="cyber-glow h-4 w-4" />
        <div className="flex-1 text-xs">
          <div className="cyber-display cyber-glow text-sm">Audit sécurité Tailscale</div>
          <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
            Le module d'audit est désormais dans la section Sécurité (checks locaux + cloud avec PAT admin).
          </div>
        </div>
        <a
          href="/security/tailscale"
          className="inline-flex items-center gap-1 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20"
        >
          Ouvrir l'audit
        </a>
      </div>

      {/* HA exit-node watchdog */}
      <TailscaleHAPanel />

      {/* Test réseau (ping + traceroute) */}
      <NetTestPanel daemonRunning={!!s?.daemon_running} />

      {/* Connect form */}
      <div className="cyber-panel space-y-4 p-5">
        <h2 className="cyber-display cyber-glow text-base">Connexion</h2>
        <p className="text-[11px] text-[color:var(--color-cyber-muted)]">
          Génère une auth key dans{" "}
          <a
            href="https://login.tailscale.com/admin/settings/keys"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[color:var(--color-cyber-accent)] hover:underline"
          >
            admin.tailscale.com → Settings → Keys
            <ExternalLink className="h-3 w-3" />
          </a>{" "}
          (reusable + tagged conseillé). Elle est stockée chiffrée en DB,
          jamais retournée en clair.
        </p>

        <Field
          label="Auth key"
          hint={
            hasAuthKey
              ? "Une auth key est déjà stockée — laisser vide pour la réutiliser, ou en coller une nouvelle pour remplacer."
              : "Format: tskey-auth-xxxxxxxxx"
          }
        >
          <input
            type="password"
            value={authKey}
            onChange={(e) => setAuthKey(e.target.value)}
            placeholder={hasAuthKey ? "(stocked, laisser vide pour réutiliser)" : "tskey-auth-..."}
            className="w-full border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
            autoComplete="off"
          />
        </Field>

        <Field label="Hostname (nom affiché dans admin Tailscale)" hint="Laisser vide = utiliser hostname système (GL-BE10000)">
          <input
            type="text"
            value={hostname}
            onChange={(e) => setHostname(e.target.value)}
            placeholder="slate-7-pro"
            className="w-full border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
          />
        </Field>

        <Field
          label="Routes annoncées (subnet routing)"
          hint="Subnets que le Slate publie aux autres peers Tailscale. Activer 'Subnet routes' dans admin Tailscale pour qu'elles soient acceptées."
        >
          <input
            type="text"
            value={advertiseRoutes}
            onChange={(e) => setAdvertiseRoutes(e.target.value)}
            placeholder="10.137.42.0/24,10.91.18.0/24,..."
            className="w-full border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
          />
        </Field>

        <Field
          label="⬇ Sortir via un peer (route par défaut → Tailscale)"
          hint="Renseigne ici si tu veux que TOUT le trafic Internet du Slate sorte par ce peer. Installe 0.0.0.0/0 sur tailscale0. Hostname ou Tailnet IP (ex: ui-etr-udm01-p ou 100.93.24.46). Vide = sortie WAN normale."
        >
          <input
            type="text"
            value={exitNode}
            onChange={(e) => setExitNode(e.target.value)}
            placeholder="ui-etr-udm01-p  OU  100.x.x.x"
            className="w-full border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
          />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Toggle
            checked={acceptRoutes}
            onChange={setAcceptRoutes}
            label="Accept routes"
            hint="Recevoir les subnet routes annoncées par les peers (ex: home LAN)"
          />
          <Toggle
            checked={acceptDns}
            onChange={setAcceptDns}
            label="Accept DNS"
            hint="Utiliser le DNS de Tailscale. Off par défaut: on a déjà AdGuard local"
          />
          <Toggle
            checked={advertiseExitNode}
            onChange={setAdvertiseExitNode}
            label="⬆ Offrir ce Slate comme exit node"
            hint="Publication uniquement: les autres peers pourront router 0.0.0.0/0 vers nous (notre WAN). N'AFFECTE PAS la route par défaut du Slate. Requiert validation dans admin.tailscale.com."
          />
          <Toggle
            checked={shieldsUp}
            onChange={setShieldsUp}
            label="Shields up"
            hint="Bloque TOUT trafic entrant des peers (Tailscale offert mais pas accessible)"
          />
        </div>

        {connectMutation.isError && (
          <div className="cyber-panel border border-red-500/40 bg-red-500/5 p-2 text-[10px] text-red-300">
            {errorMessage(connectMutation.error)}
          </div>
        )}
        {connectMutation.data?.auth_url && (
          <div className="cyber-panel border border-orange-500/40 bg-orange-500/5 p-3 text-[11px]">
            <div className="mb-1 inline-flex items-center gap-1 uppercase tracking-[0.18em] text-orange-300">
              <AlertTriangle className="h-3 w-3" /> Login browser requis
            </div>
            <a
              href={connectMutation.data.auth_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 break-all font-mono text-[color:var(--color-cyber-accent)] hover:underline"
            >
              {connectMutation.data.auth_url}
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        )}
        {connectMutation.data?.note && !connectMutation.data?.auth_url && (
          <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
            <pre className="whitespace-pre-wrap break-words font-mono text-[10px]">
              {connectMutation.data.note}
            </pre>
          </div>
        )}

        <button
          type="button"
          onClick={() => connectMutation.mutate()}
          disabled={connectMutation.isPending || (!authKey && !hasAuthKey)}
          className="inline-flex items-center gap-2 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-4 py-2 text-[11px] font-bold uppercase tracking-[0.2em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-50"
          title={
            s?.backend_state === "Running"
              ? "Re-applique la config courante au daemon (utile après avoir changé un toggle)"
              : "Démarre tailscaled + connecte au tailnet"
          }
        >
          <Zap className="h-3.5 w-3.5" />
          {connectMutation.isPending
            ? s?.backend_state === "Running"
              ? "Reconnexion…"
              : "Connexion…"
            : s?.backend_state === "Running"
              ? "Reconnect (re-apply config)"
              : "Connect"}
        </button>
      </div>

      {/* Peers */}
      {s && s.peers.length > 0 && (
        <div className="cyber-panel overflow-hidden">
          <div className="border-b border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] p-3 text-[11px] uppercase tracking-[0.18em] text-[color:var(--color-cyber-muted)]">
            Peers du tailnet ({s.peers.length})
          </div>
          <table className="w-full text-xs">
            <thead className="bg-[color:var(--color-cyber-surface)]">
              <tr>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">État</th>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">Hostname</th>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">IP</th>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">OS</th>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">Routes</th>
                <th className="cyber-label px-3 py-2 text-left text-[10px]">Exit-node</th>
              </tr>
            </thead>
            <tbody>
              {s.peers.map((p) => (
                <tr
                  key={p.dns_name || p.hostname}
                  className="border-b border-[color:var(--color-cyber-border)]/40"
                >
                  <td className="px-3 py-2">
                    <span
                      className={cn(
                        "inline-block h-2 w-2 rounded-full",
                        p.online ? "bg-emerald-400" : "bg-gray-500",
                      )}
                    />
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {p.hostname ? <ClickableHost value={p.hostname} /> : "—"}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {p.tailscale_ips[0] ? (
                      <ClickableHost value={p.tailscale_ips[0]} />
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-[color:var(--color-cyber-muted)]">
                    {p.os}
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px]">
                    {p.primary_routes.length > 0
                      ? p.primary_routes.join(", ")
                      : "—"}
                  </td>
                  <td className="px-3 py-2 text-[10px]">
                    {p.exit_node_option ? (
                      <span className="border border-emerald-500/40 px-1.5 py-[1px] uppercase tracking-[0.18em] text-emerald-300">
                        offre
                      </span>
                    ) : (
                      "—"
                    )}{" "}
                    {p.exit_node && (
                      <span className="ml-1 border border-amber-500/60 px-1.5 py-[1px] uppercase tracking-[0.18em] text-amber-300">
                        actif
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

type TestMode = "ping_icmp" | "ping_tailscale" | "traceroute";

function NetTestPanel({ daemonRunning }: { daemonRunning: boolean }) {
  const [target, setTarget] = useState("");
  const [mode, setMode] = useState<TestMode>("ping_icmp");
  const [count, setCount] = useState(3);
  const [maxHops, setMaxHops] = useState(15);

  const ping = useMutation({
    mutationFn: () =>
      pingTailscale(
        target.trim(),
        mode === "ping_tailscale" ? "tailscale" : "icmp",
        count,
      ),
  });
  const trace = useMutation({
    mutationFn: () => tracerouteTailscale(target.trim(), maxHops),
  });
  const active = mode === "traceroute" ? trace : ping;
  const result = active.data;

  const run = () => {
    if (mode === "traceroute") trace.mutate();
    else ping.mutate();
  };

  return (
    <div className="cyber-panel space-y-3 p-5">
      <h2 className="cyber-display cyber-glow text-base">Test réseau</h2>
      <p className="text-[11px] text-[color:var(--color-cyber-muted)]">
        Tests <em>depuis le Slate</em>. <strong>Ping ICMP</strong> classique ·{" "}
        <strong>Ping Tailscale</strong> overlay (montre direct vs DERP relay) ·{" "}
        <strong>Traceroute</strong> L3 (où le paquet meurt si reachability fail).
      </p>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-[2fr_auto_auto_auto]">
        <input
          type="text"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="10.13.14.1  /  wraith-7  /  1.1.1.1"
          className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 font-mono text-xs text-[color:var(--color-cyber-fg)] outline-none focus:border-[color:var(--color-cyber-accent)]"
          onKeyDown={(e) => {
            if (e.key === "Enter" && target.trim() && !active.isPending) run();
          }}
        />
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as TestMode)}
          className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 text-xs"
        >
          <option value="ping_icmp">ping ICMP</option>
          <option value="ping_tailscale" disabled={!daemonRunning}>
            ping Tailscale
          </option>
          <option value="traceroute">traceroute</option>
        </select>
        {mode === "traceroute" ? (
          <select
            value={maxHops}
            onChange={(e) => setMaxHops(Number(e.target.value))}
            className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 text-xs"
          >
            {[8, 15, 20, 30].map((n) => (
              <option key={n} value={n}>
                max {n} hops
              </option>
            ))}
          </select>
        ) : (
          <select
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            className="border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)] px-2 py-1.5 text-xs"
          >
            {[1, 3, 5, 10].map((n) => (
              <option key={n} value={n}>
                {n} probe{n > 1 ? "s" : ""}
              </option>
            ))}
          </select>
        )}
        <button
          type="button"
          onClick={run}
          disabled={active.isPending || !target.trim()}
          className="inline-flex items-center justify-center gap-1 border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[color:var(--color-cyber-accent)] hover:bg-[color:var(--color-cyber-accent)]/20 disabled:opacity-50"
        >
          {active.isPending
            ? mode === "traceroute"
              ? "trace…"
              : "ping…"
            : mode === "traceroute"
              ? "Trace"
              : "Ping"}
        </button>
      </div>

      {active.isError && (
        <div className="cyber-panel border border-red-500/40 bg-red-500/5 p-2 text-[10px] text-red-300">
          {errorMessage(active.error)}
        </div>
      )}
      {result && (
        <div
          className={cn(
            "cyber-panel p-3",
            result.ok
              ? "border border-emerald-500/40 bg-emerald-500/5"
              : "border border-red-500/40 bg-red-500/5",
          )}
        >
          <div
            className={cn(
              "mb-1 inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.18em]",
              result.ok ? "text-emerald-300" : "text-red-300",
            )}
          >
            {result.ok ? (
              <CheckCircle2 className="h-3 w-3" />
            ) : (
              <AlertTriangle className="h-3 w-3" />
            )}
            {mode.replace("_", " ")} → {result.target} :{" "}
            {result.ok ? "ok" : "fail"}
          </div>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[10px] text-[color:var(--color-cyber-fg)]">
            {result.output}
          </pre>
        </div>
      )}

      {/* Quick presets */}
      <div className="flex flex-wrap gap-1 text-[9px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
        <span className="mt-1">tests rapides :</span>
        {[
          { label: "1.1.1.1 (internet)", t: "1.1.1.1" },
          { label: "10.137.42.1 (LAN main)", t: "10.137.42.1" },
          { label: "10.13.14.1 (home via TS)", t: "10.13.14.1" },
          { label: "100.93.24.46 (UDM-P)", t: "100.93.24.46" },
        ].map((p) => (
          <button
            key={p.t}
            type="button"
            onClick={() => setTarget(p.t)}
            className="border border-[color:var(--color-cyber-border)] px-2 py-0.5 hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function Info({
  label,
  value,
  accent = false,
}: {
  label: string;
  /** Accepts a ReactNode so callers can pass a `<ClickableHost />` for
   *  IP/hostname fields without breaking the layout. */
  value: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <div>
      <div className="cyber-label mb-1 text-[10px]">{label}</div>
      <div
        className={cn(
          "font-mono text-xs",
          accent
            ? "text-[color:var(--color-cyber-accent)] cyber-glow"
            : "text-[color:var(--color-cyber-fg)]",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="cyber-label mb-1 block text-[10px]">{label}</label>
      {children}
      {hint && (
        <div className="mt-1 text-[10px] text-[color:var(--color-cyber-muted)]">
          {hint}
        </div>
      )}
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 border border-[color:var(--color-cyber-border)] p-2 hover:border-[color:var(--color-cyber-accent)]">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1 accent-[color:var(--color-cyber-accent)]"
      />
      <div className="text-[10px]">
        <div className="uppercase tracking-[0.18em] text-[color:var(--color-cyber-fg)]">
          {label}
        </div>
        {hint && (
          <div className="mt-0.5 text-[9px] text-[color:var(--color-cyber-muted)]">
            {hint}
          </div>
        )}
      </div>
    </label>
  );
}

