import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Globe,
  Network as NetworkIcon,
  Pencil,
  Plus,
  Settings2,
  Share2,
  Shield,
  ShieldOff,
  Terminal,
  Trash2,
  X,
} from "lucide-react";
import {
  createNetwork,
  deleteNetwork,
  listNetworks,
  updateNetwork,
} from "@/api/networks";
import { ClickableHost } from "@/components/ClickableHost";
import DnsProtectionWidget from "@/components/DnsProtectionWidget";
import type { NetworkPublic, NetworkWrite } from "@/types/network";
import { useT } from "@/lib/i18n";
import { errorMessage } from "@/lib/error-utils";


function NetworkForm({
  initial,
  allNetworks,
  onClose,
}: {
  initial?: NetworkPublic;
  /** Used to render the "reachable_networks" checkbox grid — every
   *  network EXCEPT the one being edited shows up as a togglable peer. */
  allNetworks: NetworkPublic[];
  onClose: () => void;
}) {
  const isEdit = Boolean(initial);
  const [slug, setSlug] = useState(initial?.slug ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [bridgeName, setBridgeName] = useState(initial?.bridge_name ?? "br-");
  const [subnet, setSubnet] = useState(initial?.subnet_cidr ?? "192.168.20.0/24");
  const [gateway, setGateway] = useState(initial?.gateway_ip ?? "192.168.20.1");
  const [dhcp, setDhcp] = useState(initial?.dhcp_enabled ?? true);
  const [vlanTag, setVlanTag] = useState<string>(
    initial?.vlan_tag ? String(initial.vlan_tag) : "",
  );
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [ipv6Enabled, setIpv6Enabled] = useState(initial?.ipv6_enabled ?? false);
  const [ipv6Subnet, setIpv6Subnet] = useState(initial?.ipv6_subnet_cidr ?? "");

  // ── 3-level isolation state ────────────────────────────────────
  const [intraBridge, setIntraBridge] = useState(
    initial?.intra_bridge_isolation ?? false,
  );
  const [reachInternet, setReachInternet] = useState(
    initial?.reach_internet ?? true,
  );
  const [reachable, setReachable] = useState<Set<string>>(
    new Set(initial?.reachable_networks ?? []),
  );
  // Admin/management plane split per service. Defaults mirror the
  // backend Pydantic defaults : services ON, UI + SSH OFF.
  const [servicesAccess, setServicesAccess] = useState(
    initial?.services_access ?? true,
  );
  const [adminUiAccess, setAdminUiAccess] = useState(
    initial?.admin_ui_access ?? false,
  );
  const [sshAccess, setSshAccess] = useState(
    initial?.ssh_access ?? false,
  );
  // Tailnet subnet routing — toggle whether this network's CIDR is
  // advertised on the tailnet via `tailscale --advertise-routes`.
  const [exposeToTailnet, setExposeToTailnet] = useState(
    initial?.expose_to_tailnet ?? false,
  );

  // Per-network Tor. `off` is the sane default — opt in per network.
  // `transparent` redirects every TCP via Tor's TransPort (slow, anonymous).
  // `socks_only` keeps direct WAN but exposes SOCKS5 on the gateway IP.
  const [torMode, setTorMode] = useState<"off" | "transparent" | "socks_only">(
    initial?.tor_route_mode ?? "off",
  );
  const [torDnsOverTor, setTorDnsOverTor] = useState(
    initial?.tor_dns_over_tor ?? false,
  );
  const [torKillSwitch, setTorKillSwitch] = useState(
    initial?.tor_kill_switch ?? false,
  );

  // Peers candidate to "reachable_networks" : every other network in
  // the catalog. We exclude the current one (no self-routing).
  const peers = useMemo(
    () =>
      allNetworks
        .filter((n) => n.slug !== (initial?.slug ?? slug))
        .sort((a, b) => a.slug.localeCompare(b.slug)),
    [allNetworks, initial, slug],
  );

  const queryClient = useQueryClient();

  const submit = useMutation({
    mutationFn: () => {
      const body: NetworkWrite = {
        display_name: displayName,
        bridge_name: bridgeName,
        subnet_cidr: subnet,
        gateway_ip: gateway,
        dhcp_enabled: dhcp,
        vlan_tag: vlanTag ? Number(vlanTag) : null,
        notes,
        ipv6_enabled: ipv6Enabled,
        ipv6_subnet_cidr: ipv6Subnet,
        intra_bridge_isolation: intraBridge,
        reach_internet: reachInternet,
        reachable_networks: Array.from(reachable),
        services_access: servicesAccess,
        admin_ui_access: adminUiAccess,
        ssh_access: sshAccess,
        expose_to_tailnet: exposeToTailnet,
        tor_route_mode: torMode,
        tor_dns_over_tor: torDnsOverTor,
        tor_kill_switch: torKillSwitch,
      };
      return isEdit
        ? updateNetwork(initial!.slug, body)
        : createNetwork({ ...body, slug });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["networks"] });
      onClose();
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit.mutate();
  }

  return (
    <form
      onSubmit={onSubmit}
      className="cyber-card cyber-card-accent space-y-4 p-5"
    >
      <div className="mb-2 flex items-center justify-between">
        <h3 className="cyber-display cyber-glow text-lg">
          {isEdit ? `EDIT NETWORK · ${initial!.slug}` : "NEW NETWORK"}
        </h3>
        <button
          type="button"
          onClick={onClose}
          className="border border-transparent p-1.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="cyber-label mb-1.5 block">slug</span>
          <input
            type="text"
            required
            disabled={isEdit}
            value={slug}
            onChange={(e) =>
              setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))
            }
            placeholder="media-vlan"
            className="cyber-input w-full py-2 px-3 text-sm font-mono disabled:opacity-50"
          />
        </label>
        <label className="block">
          <span className="cyber-label mb-1.5 block">display name</span>
          <input
            type="text"
            required
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Media VLAN"
            className="cyber-input w-full py-2 px-3 text-sm"
          />
        </label>
        <label className="block">
          <span className="cyber-label mb-1.5 block">bridge name</span>
          <input
            type="text"
            required
            value={bridgeName}
            onChange={(e) => setBridgeName(e.target.value)}
            placeholder="br-media"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
        <label className="block">
          <span className="cyber-label mb-1.5 block">vlan tag (optionnel)</span>
          <input
            type="number"
            min={1}
            max={4094}
            value={vlanTag}
            onChange={(e) => setVlanTag(e.target.value)}
            placeholder="42"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
        <label className="block">
          <span className="cyber-label mb-1.5 block">subnet (CIDR)</span>
          <input
            type="text"
            required
            value={subnet}
            onChange={(e) => setSubnet(e.target.value)}
            placeholder="192.168.20.0/24"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
        <label className="block">
          <span className="cyber-label mb-1.5 block">gateway ip</span>
          <input
            type="text"
            value={gateway}
            onChange={(e) => setGateway(e.target.value)}
            placeholder="192.168.20.1"
            className="cyber-input w-full py-2 px-3 text-sm font-mono"
          />
        </label>
      </div>

      <div className="flex flex-wrap gap-6">
        <label className="flex items-center gap-2 text-xs uppercase tracking-[0.15em] text-[color:var(--color-cyber-fg)]">
          <input
            type="checkbox"
            checked={dhcp}
            onChange={(e) => setDhcp(e.target.checked)}
            className="h-4 w-4 accent-[color:var(--color-cyber-accent)]"
          />
          dhcp enabled
        </label>
      </div>

      {/* ── ISOLATION ────────────────────────────────────────────────
          3-dimension model. Each section maps to a separate underlying
          mechanism — confusing them together cost us a bug session, so
          we keep the UI explicit. */}
      <div className="border border-[color:var(--color-cyber-border-strong)] bg-[color:var(--color-cyber-bg-2)]/40 p-4">
        <div className="cyber-label mb-3 flex items-center gap-2">
          <Shield className="h-3 w-3" />
          isolation · 3 niveaux
        </div>

        {/* L2 — intra-bridge ports */}
        <div className="mb-4">
          <div className="cyber-label !text-[9px] mb-1.5 text-[color:var(--color-cyber-muted)]">
            L2 · entre ports du même bridge
          </div>
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={intraBridge}
              onChange={(e) => setIntraBridge(e.target.checked)}
              className="h-4 w-4 accent-[color:var(--color-cyber-accent)]"
            />
            <span>
              cloisonner les ports du bridge
              <span className="ml-2 text-[10px] text-[color:var(--color-cyber-dim)]">
                (rare — généralement on préfère bridges séparés)
              </span>
            </span>
          </label>
        </div>

        {/* L3 — internet + peer networks */}
        <div className="mb-4">
          <div className="cyber-label !text-[9px] mb-1.5 text-[color:var(--color-cyber-muted)]">
            L3 · accès vers autres zones
          </div>
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={reachInternet}
              onChange={(e) => setReachInternet(e.target.checked)}
              className="h-4 w-4 accent-[color:var(--color-cyber-accent)]"
            />
            <Globe className="h-3 w-3" />
            <span>
              accès internet (WAN)
              <span className="ml-2 text-[10px] text-[color:var(--color-cyber-dim)]">
                (sortie vers le net via le routeur)
              </span>
            </span>
          </label>

          {peers.length > 0 && (
            <>
              <div className="mt-3 mb-1.5 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-muted)]">
                réseaux atteignables depuis {slug || "ce réseau"} :
              </div>
              <div className="grid grid-cols-2 gap-1.5 ml-6">
                {peers.map((p) => (
                  <label
                    key={p.slug}
                    className="flex items-center gap-2 text-[11px]"
                  >
                    <input
                      type="checkbox"
                      checked={reachable.has(p.slug)}
                      onChange={(e) => {
                        const next = new Set(reachable);
                        if (e.target.checked) next.add(p.slug);
                        else next.delete(p.slug);
                        setReachable(next);
                      }}
                      className="h-3.5 w-3.5 accent-[color:var(--color-cyber-accent)]"
                    />
                    <span className="font-mono text-[color:var(--color-cyber-fg)]">
                      {p.slug}
                    </span>
                    <span className="text-[9px] text-[color:var(--color-cyber-dim)]">
                      {p.subnet_cidr}
                    </span>
                  </label>
                ))}
              </div>
              <p className="mt-2 ml-6 text-[10px] text-[color:var(--color-cyber-dim)]">
                {reachable.size === 0
                  ? "▸ aucun → isolé de tous les autres subnets (sauf internet si coché)"
                  : `▸ peut router vers ${reachable.size} autre(s) réseau(x)`}
              </p>
            </>
          )}
        </div>

        {/* Admin / management plane — split per service so guest
            networks can keep DHCP/DNS without exposing LuCI or SSH. */}
        <div>
          <div className="cyber-label !text-[9px] mb-1.5 text-[color:var(--color-cyber-muted)]">
            administration · plan par service
          </div>

          <label className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              checked={servicesAccess}
              onChange={(e) => setServicesAccess(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-[color:var(--color-cyber-accent)]"
            />
            <span>
              <span className="inline-flex items-center gap-1.5">
                <Settings2 className="h-3 w-3" />
                services essentiels — DHCP · DNS · ICMP
              </span>
              {!servicesAccess && (
                <span className="ml-2 text-[10px] text-red-300">
                  ⚠ sans ça, pas de DHCP — les clients n'auront pas d'IP
                </span>
              )}
              <span className="block text-[10px] text-[color:var(--color-cyber-dim)]">
                ▸ dnsmasq + serveur DHCP local, ping vers la gateway
              </span>
            </span>
          </label>

          <label className="mt-2 flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              checked={adminUiAccess}
              onChange={(e) => setAdminUiAccess(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-[color:var(--color-cyber-accent)]"
            />
            <span>
              <span className="inline-flex items-center gap-1.5">
                <Shield className="h-3 w-3" />
                UI admin — LuCI + GL.iNet UI (TCP 80/443)
              </span>
              <span className="block text-[10px] text-[color:var(--color-cyber-dim)]">
                ▸ à activer uniquement pour les réseaux de confiance
              </span>
            </span>
          </label>

          <label className="mt-2 flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              checked={sshAccess}
              onChange={(e) => setSshAccess(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-[color:var(--color-cyber-accent)]"
            />
            <span>
              <span className="inline-flex items-center gap-1.5">
                <Terminal className="h-3 w-3" />
                SSH — dropbear (TCP 22)
              </span>
              <span className="block text-[10px] text-[color:var(--color-cyber-dim)]">
                ▸ opt-in explicite, ops uniquement
              </span>
            </span>
          </label>
        </div>

        {/* Tailnet subnet routing — orthogonal au plan admin. Met le
            CIDR du réseau dans `tailscale --advertise-routes`, sans
            ouvrir aucun port admin local. */}
        <div className="mt-4 border-t border-[color:var(--color-cyber-border)] pt-3">
          <div className="cyber-label !text-[9px] mb-1.5 text-[color:var(--color-cyber-muted)]">
            tailnet · routage subnet
          </div>
          <label className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              checked={exposeToTailnet}
              onChange={(e) => setExposeToTailnet(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-[color:var(--color-cyber-accent)]"
            />
            <span>
              <span className="inline-flex items-center gap-1.5">
                <Share2 className="h-3 w-3" />
                exposer ce réseau sur le tailnet
              </span>
              <span className="block text-[10px] text-[color:var(--color-cyber-dim)]">
                ▸ ajoute le CIDR à `tailscale --advertise-routes`, joignable
                depuis tes peers tailnet (téléphone, laptop…)
              </span>
            </span>
          </label>
        </div>

        {/* Per-network Tor. The global daemon switch + bridges live in
            TorStatusCard at the top of the page; here we decide IF this
            specific subnet is routed through Tor (and how). */}
        <div className="mt-4 border-t border-purple-500/30 pt-3">
          <div className="cyber-label !text-[9px] mb-1.5 text-purple-300">
            tor · routage per-réseau
          </div>
          <select
            value={torMode}
            onChange={(e) => setTorMode(e.target.value as typeof torMode)}
            className="cyber-input w-full text-xs"
          >
            <option value="off">off — pas de Tor pour ce réseau</option>
            <option value="transparent">
              transparent — tout le trafic via Tor (lent, anonyme)
            </option>
            <option value="socks_only">
              socks_only — SOCKS5 sur la gateway, opt-in par app
            </option>
          </select>
          {torMode === "transparent" && (
            <div className="mt-2 space-y-1.5 rounded border border-purple-500/40 bg-purple-950/20 p-2 text-[11px]">
              <p className="text-purple-200">
                ⚠ Latence ↑ (250-800 ms), débit plafonné (~1-3 Mbps). Plein de
                sites bloquent les exit IPs Tor.
              </p>
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={torDnsOverTor}
                  onChange={(e) => setTorDnsOverTor(e.target.checked)}
                  className="mt-0.5 h-4 w-4 accent-purple-400"
                />
                <span>
                  <strong>DNS-over-Tor</strong> — redirige les requêtes DNS
                  via Tor (évite les fuites au resolver amont).
                </span>
              </label>
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={torKillSwitch}
                  onChange={(e) => setTorKillSwitch(e.target.checked)}
                  className="mt-0.5 h-4 w-4 accent-purple-400"
                />
                <span>
                  <strong>Kill-switch</strong> — si le daemon Tor crashe,
                  bloque la sortie WAN de ce réseau (fail-closed). Anti-fuite
                  d'IP réelle.
                </span>
              </label>
            </div>
          )}
          {torMode === "socks_only" && (
            <p className="mt-2 text-[11px] text-purple-300">
              ▸ Configure ton navigateur sur{" "}
              <code>socks5://{gateway || "&lt;gateway&gt;"}:9050</code>
            </p>
          )}
        </div>
      </div>

      {/* IPv6 section */}
      <div className="border-t border-[color:var(--color-cyber-border)] pt-4">
        <label className="flex items-center gap-2 text-xs uppercase tracking-[0.15em] text-[color:var(--color-cyber-fg)]">
          <input
            type="checkbox"
            checked={ipv6Enabled}
            onChange={(e) => setIpv6Enabled(e.target.checked)}
            className="h-4 w-4 accent-[color:var(--color-cyber-accent)]"
          />
          ipv6 enabled
        </label>
        {ipv6Enabled && (
          <div className="mt-3">
            <label className="block">
              <span className="cyber-label mb-1.5 block">
                ipv6 subnet (vide = SLAAC / WAN delegation)
              </span>
              <input
                type="text"
                value={ipv6Subnet}
                onChange={(e) => setIpv6Subnet(e.target.value)}
                placeholder="fd00:abcd:1234::/64"
                className="cyber-input w-full py-2 px-3 text-sm font-mono"
              />
            </label>
            <p className="mt-1 text-[10px] uppercase tracking-[0.15em] text-[color:var(--color-cyber-dim)]">
              ▸ vide = le bridge récupère un /64 du préfixe délégué par le WAN (mode standard fibre)
            </p>
          </div>
        )}
      </div>

      <label className="block">
        <span className="cyber-label mb-1.5 block">notes</span>
        <input
          type="text"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="usage typique"
          className="cyber-input w-full py-2 px-3 text-sm"
        />
      </label>

      {submit.error && (
        <p className="cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(submit.error)}
        </p>
      )}

      <div className="flex gap-3">
        <button
          type="submit"
          disabled={submit.isPending}
          className="cyber-button flex-1 px-4 py-2.5 text-sm"
        >
          {submit.isPending ? "// saving…" : isEdit ? "Enregistrer ▸" : "Créer ▸"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="cyber-button-ghost px-4 py-2.5 text-xs"
        >
          Annuler
        </button>
      </div>
    </form>
  );
}

function NetworkCard({
  network,
  onEdit,
  onDeleted,
}: {
  network: NetworkPublic;
  onEdit: () => void;
  onDeleted: () => void;
}) {
  const del = useMutation({
    mutationFn: () => deleteNetwork(network.slug),
    onSuccess: onDeleted,
  });

  return (
    <article className="cyber-card p-5">
      <div className="flex items-start gap-3">
        <div className="cyber-glow flex h-10 w-10 shrink-0 items-center justify-center border border-[color:var(--color-cyber-accent)] bg-[color:var(--color-cyber-accent)]/10">
          <NetworkIcon className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <h3 className="cyber-display cyber-glow text-base">{network.slug}</h3>
            {network.vlan_tag != null && (
              <span className="cyber-chip">vlan {network.vlan_tag}</span>
            )}
            {!network.reach_internet && (
              <span className="cyber-chip cyber-chip-warn" title="Pas de sortie WAN">
                <ShieldOff className="mr-1 inline h-2.5 w-2.5" />
                no internet
              </span>
            )}
            {network.reach_internet &&
              network.reachable_networks.length === 0 && (
                <span
                  className="cyber-chip cyber-chip-warn"
                  title="Pas de route vers les autres subnets"
                >
                  isolé L3
                </span>
              )}
            {network.intra_bridge_isolation && (
              <span className="cyber-chip cyber-chip-warn">L2 cloisonné</span>
            )}
            {!network.services_access && (
              <span
                className="cyber-chip cyber-chip-on"
                title="DHCP/DNS bloqués — clients sans IP"
              >
                no svc
              </span>
            )}
            {network.admin_ui_access && (
              <span
                className="cyber-chip cyber-chip-warn"
                title="LuCI / GL.iNet UI accessibles depuis ce réseau"
              >
                UI admin
              </span>
            )}
            {network.ssh_access && (
              <span
                className="cyber-chip cyber-chip-warn"
                title="SSH (dropbear) accessible depuis ce réseau"
              >
                SSH
              </span>
            )}
            {network.expose_to_tailnet && (
              <span
                className="cyber-chip cyber-chip-ok"
                title="CIDR annoncé sur le tailnet (tailscale --advertise-routes)"
              >
                <Share2 className="mr-1 inline h-2.5 w-2.5" />
                tailnet
              </span>
            )}
            {!network.dhcp_enabled && (
              <span className="cyber-chip">no DHCP</span>
            )}
            {network.ipv6_enabled ? (
              <span className="cyber-chip cyber-chip-ok">ipv6</span>
            ) : (
              <span className="cyber-chip">ipv4 only</span>
            )}
          </div>
          <p className="mt-1 text-xs text-[color:var(--color-cyber-muted)]">
            {network.display_name}
          </p>
          <div className="mt-2 grid grid-cols-1 gap-x-4 gap-y-0.5 text-[11px] sm:grid-cols-2">
            <span>
              bridge{" "}
              <span className="cyber-glow-soft font-mono">{network.bridge_name}</span>
            </span>
            <span>
              subnet{" "}
              <span className="cyber-glow-soft font-mono">{network.subnet_cidr}</span>
            </span>
            <span>
              gw{" "}
              <span className="cyber-glow-soft font-mono">
                {network.gateway_ip ? (
                  <ClickableHost value={network.gateway_ip} />
                ) : (
                  "—"
                )}
              </span>
            </span>
            {network.ipv6_enabled && (
              <span>
                ipv6{" "}
                <span className="cyber-glow-soft font-mono">
                  {network.ipv6_subnet_cidr || "auto (PD)"}
                </span>
              </span>
            )}
          </div>
          {network.notes && (
            <p className="mt-2 text-[11px] italic text-[color:var(--color-cyber-dim)]">
              {network.notes}
            </p>
          )}
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            type="button"
            onClick={onEdit}
            className="border border-transparent p-2 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => {
              if (confirm(`Supprimer le réseau "${network.slug}" ?`))
                del.mutate();
            }}
            disabled={del.isPending}
            className="border border-transparent p-2 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)] disabled:opacity-40"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* DNS protection widget — per-network DoT/DoH + AdGuard client.
          Lives here (not in profiles) because the protection follows the
          network's nature: invité = famille, IoT = unfiltered, admin =
          standard, etc. See [[dns/manager]]. */}
      <DnsProtectionWidget
        networkSlug={network.slug}
        networkName={network.display_name}
      />

      {del.error && (
        <p className="mt-3 cyber-chip cyber-chip-on block !rounded-none px-3 py-2 text-xs">
          {errorMessage(del.error)}
        </p>
      )}
    </article>
  );
}

export default function Networks() {
  const t = useT();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<NetworkPublic | null>(null);
  const [creating, setCreating] = useState(false);

  const networks = useQuery({ queryKey: ["networks"], queryFn: listNetworks });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["networks"] });
  const closeForm = () => {
    setEditing(null);
    setCreating(false);
  };

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="cyber-label mb-2 flex items-center gap-2">
            <NetworkIcon className="cyber-glow h-3 w-3" />
            {t("networks.counter", { n: networks.data?.length ?? 0 })}
          </div>
          <h1
            className="cyber-display cyber-glitch text-4xl"
            data-text={t("networks.title").toUpperCase()}
          >
            {t("networks.title").toUpperCase()}
          </h1>
          <p className="mt-2 text-[11px] uppercase tracking-[0.2em] text-[color:var(--color-cyber-muted)]">
            {t("networks.subtitle")}
          </p>
        </div>
        {!creating && !editing && (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="cyber-button inline-flex items-center gap-2 px-4 py-2.5 text-xs"
          >
            <Plus className="h-3.5 w-3.5" />
            {t("networks.new")}
          </button>
        )}
      </header>

      {/* Tor lives in its own section now — see /networks/tor (Réseau →
          Tor) for the daemon status, install, bridges and the per-network
          summary. The per-network routing toggle (off / transparent /
          socks_only + DNS-over-Tor + kill-switch) stays in the network's
          edit form below — that's where it belongs. */}

      {creating && <section className="mb-6">
        <NetworkForm
          allNetworks={networks.data ?? []}
          onClose={closeForm}
        />
      </section>}
      {editing && <section className="mb-6">
        <NetworkForm
          initial={editing}
          allNetworks={networks.data ?? []}
          onClose={closeForm}
        />
      </section>}

      {networks.isLoading && (
        <p className="cyber-label cyber-cursor">{t("common.loading")}</p>
      )}

      {networks.data && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {networks.data.map((n) => (
            <NetworkCard
              key={n.slug}
              network={n}
              onEdit={() => {
                setCreating(false);
                setEditing(n);
              }}
              onDeleted={refresh}
            />
          ))}
        </div>
      )}
    </div>
  );
}
