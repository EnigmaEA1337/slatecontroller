import { FormEvent, useMemo, useState } from "react";
import { createPortal } from "react-dom";
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
import {
  getReverseRouting,
  listAppPresets,
  listTailnetDestinationCandidates,
  reconcileDnsRouting,
  reconcileReverseRouting,
  type AppPreset,
} from "@/api/tailscale";
import { ClickableHost } from "@/components/ClickableHost";
import DnsProtectionWidget from "@/components/DnsProtectionWidget";
import type {
  NetworkPublic,
  NetworkWrite,
  TailnetDestinationVia,
} from "@/types/network";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
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

  // Per-destination reverse routing : which subnets THIS LAN is allowed
  // to reach, with NAT mode AND egress path per entry.
  //   mode : "off" | "routed" | "snat" ("off" omits the entry on save)
  //   via  : "tailnet" | "wan" | "proton" | "tor"
  // Form state lives as two parallel maps indexed by CIDR so the rest of
  // the rendering code can read either independently.
  const initialDestState: Record<string, "off" | "routed" | "snat"> = {};
  const initialDestVia: Record<string, "tailnet" | "wan" | "proton" | "tor"> = {};
  const initialDestLabel: Record<string, string> = {};
  for (const d of initial?.tailnet_destinations ?? []) {
    initialDestState[d.cidr] = d.mode;
    initialDestVia[d.cidr] = d.via ?? "tailnet";
    if (d.label) initialDestLabel[d.cidr] = d.label;
  }
  const [tailnetDestState, setTailnetDestState] = useState<
    Record<string, "off" | "routed" | "snat">
  >(initialDestState);
  const [tailnetDestVia, setTailnetDestVia] = useState<
    Record<string, "tailnet" | "wan" | "proton" | "tor">
  >(initialDestVia);
  /** Label per CIDR — pure UI metadata used to display the source app
   *  for destinations imported from a preset. */
  const [tailnetDestLabel, setTailnetDestLabel] = useState<
    Record<string, string>
  >(initialDestLabel);
  const appPresetsQ = useQuery({
    queryKey: ["tailscale", "app-presets"],
    queryFn: listAppPresets,
    staleTime: 5 * 60_000,
  });
  const [presetModalOpen, setPresetModalOpen] = useState(false);

  // Domain-based routing rules — independent list from
  // tailnet_destinations. Each rule = (label, domains[], mode, via).
  const [domainRules, setDomainRules] = useState<
    {
      label: string;
      domains: string[];
      mode: "routed" | "snat";
      via: "tailnet" | "wan" | "proton" | "tor";
    }[]
  >(
    (initial?.domain_routing_rules ?? []).map((r) => ({
      label: r.label,
      domains: [...r.domains],
      mode: r.mode,
      via: r.via,
    })),
  );
  const [newDomainRuleLabel, setNewDomainRuleLabel] = useState("");
  const [newDomainRuleDomains, setNewDomainRuleDomains] = useState("");
  const [domainRuleError, setDomainRuleError] = useState<string | null>(null);
  const tailnetDestQ = useQuery({
    queryKey: ["tailnet", "destinations"],
    queryFn: listTailnetDestinationCandidates,
    staleTime: 30_000,
  });
  // Live state of the egress paths — used to enable/disable the « via »
  // dropdown options. Cached and shared across NetworkCards.
  const routingStateQ = useQuery({
    queryKey: ["tailscale", "forwarding"],
    queryFn: getReverseRouting,
    staleTime: 30_000,
  });
  const wanReady = !!routingStateQ.data?.wan_iface;
  const protonReady = !!routingStateQ.data?.proton_iface;
  const torReady = !!routingStateQ.data?.tor_active;
  // CIDR custom en cours de saisie (entrée libre). Quand l'opérateur
  // clique « Ajouter », on injecte juste l'entrée dans tailnetDestState
  // (par défaut mode 'snat' qui est le mode le plus permissif et qui
  // marche sans coopération du peer distant).
  const [newDestCidr, setNewDestCidr] = useState("");
  const [newDestError, setNewDestError] = useState<string | null>(null);
  // Sanity check on CIDR : two checks suffit pour intercepter les
  // typos (vraie validation se fait côté backend reconcile via iptables).
  const isValidCidr = (s: string) =>
    /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/.test(s) &&
    s.split(".").every((p, i) => {
      const n = parseInt(i === 3 ? p.split("/")[0]! : p, 10);
      return n >= 0 && n <= 255;
    }) &&
    parseInt(s.split("/")[1]!, 10) <= 32;

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
        // Convert the {cidr -> mode|"off"} form-state into the
        // canonical list excluding off entries.
        tailnet_destinations: Object.entries(tailnetDestState)
          .filter(([, mode]) => mode === "routed" || mode === "snat")
          .map(([cidr, mode]) => ({
            cidr,
            mode,
            via: tailnetDestVia[cidr] ?? "tailnet",
            label: tailnetDestLabel[cidr] ?? "",
          })) as {
          cidr: string;
          mode: "routed" | "snat";
          via: "tailnet" | "wan" | "proton" | "tor";
          label: string;
        }[],
        domain_routing_rules: domainRules,
        tor_route_mode: torMode,
        tor_dns_over_tor: torDnsOverTor,
        tor_kill_switch: torKillSwitch,
      };
      return isEdit
        ? updateNetwork(initial!.slug, body)
        : createNetwork({ ...body, slug });
    },
    onSuccess: async () => {
      queryClient.invalidateQueries({ queryKey: ["networks"] });
      // After saving the catalog row, push the firewall reconciliation
      // so the new tailnet_destinations take effect on the Slate. We
      // fire-and-forget the error : if the SSH push fails (no Slate
      // online), the row is still saved and a later reconcile will
      // catch up.
      try {
        await reconcileReverseRouting();
        queryClient.invalidateQueries({ queryKey: ["tailscale", "forwarding"] });
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn("post-save reverse routing reconcile failed", e);
      }
      try {
        await reconcileDnsRouting();
        queryClient.invalidateQueries({ queryKey: ["tailscale", "dns-routing"] });
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn("post-save DNS routing reconcile failed", e);
      }
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
              <span className="block text-[10px] text-[color:var(--color-cyber-muted)]">
                ▸ ajoute le CIDR à `tailscale --advertise-routes`, joignable
                depuis tes peers tailnet (téléphone, laptop…)
              </span>
            </span>
          </label>

          {/* Per-destination reverse routing : grille cochable des
              subnets que les pairs Tailscale annoncent. Pour chaque
              entrée l'opérateur choisit Désactivé / Routé / NAT. */}
          <div className="mt-3 rounded border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]/30 p-2">
            <div className="cyber-label !text-[9px] mb-1 text-[color:var(--color-cyber-muted)]">
              destinations atteignables depuis ce réseau (routage inverse)
            </div>
            <p className="mb-2 text-[10px] text-[color:var(--color-cyber-muted)] max-w-prose">
              Choisir, pour chaque sous-réseau annoncé par les peers
              Tailscale, si les clients de ce réseau peuvent l'atteindre,
              et avec quel mode :
              <strong> Routé</strong> conserve l'IP source originale (le
              peer distant doit accepter la route),
              <strong> NAT</strong> réécrit la source vers l'IP Tailscale
              du Slate (marche partout sans config côté peer).
            </p>
            {tailnetDestQ.isLoading && (
              <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
                Lecture des destinations…
              </div>
            )}
            {tailnetDestQ.isError && (
              <div className="text-[10px] text-[color:var(--color-cyber-warn)]">
                ⚠ Tailscale injoignable : impossible de lister les
                destinations.
              </div>
            )}
            {/*
              Lignes affichées = union de :
                - destinations découvertes via tailscale status (avec
                  leurs peers annonceurs)
                - destinations « custom » présentes dans le state local
                  (saisies manuellement par l'opérateur ou héritées d'un
                  initial.tailnet_destinations qui contient un CIDR pas
                  encore annoncé par un peer)
              Une destination custom marque `discovered=false` → la
              colonne « annoncée par » indique « personnalisée » + un
              bouton ✕ pour la retirer (les découvertes ne se retirent
              pas : leur mode = "off" les enlève déjà).
            */}
            {(() => {
              const rows: {
                cidr: string;
                peers: string[];
                discovered: boolean;
                label: string;
              }[] = [];
              const seen = new Set<string>();
              for (const d of tailnetDestQ.data ?? []) {
                rows.push({
                  cidr: d.cidr,
                  peers: d.peers,
                  discovered: true,
                  label: tailnetDestLabel[d.cidr] ?? "",
                });
                seen.add(d.cidr);
              }
              for (const cidr of Object.keys(tailnetDestState)) {
                if (seen.has(cidr)) continue;
                const lbl = tailnetDestLabel[cidr] ?? "";
                rows.push({
                  cidr,
                  peers: [lbl ? `📦 ${lbl}` : "personnalisée"],
                  discovered: false,
                  label: lbl,
                });
              }
              // Group by label first (apps grouped together), CIDR within.
              rows.sort((a, b) =>
                (a.label || "zzz").localeCompare(b.label || "zzz") ||
                a.cidr.localeCompare(b.cidr),
              );
              return rows.length > 0 ? (
                <table className="cyber-table">
                  <colgroup>
                    <col />
                    <col className="w-32" />
                    <col className="w-48" />
                    <col className="w-32" />
                    <col className="w-8" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Destination</th>
                      <th>Source</th>
                      <th>Mode</th>
                      <th>Sortie via</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((d) => {
                      const m = tailnetDestState[d.cidr] ?? "off";
                      const v = tailnetDestVia[d.cidr] ?? "tailnet";
                      return (
                        <tr key={d.cidr}>
                          <td className="font-mono text-[10px]">{d.cidr}</td>
                          <td className="font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                            {d.peers.join(", ")}
                          </td>
                          <td>
                            <div className="flex gap-3 text-[10px]">
                              {(["off", "routed", "snat"] as const).map(
                                (val) => (
                                  <label
                                    key={val}
                                    className="inline-flex items-center gap-1 cursor-pointer"
                                  >
                                    <input
                                      type="radio"
                                      name={`tsdest-${d.cidr}`}
                                      checked={m === val}
                                      onChange={() =>
                                        setTailnetDestState((prev) => ({
                                          ...prev,
                                          [d.cidr]: val,
                                        }))
                                      }
                                    />
                                    <span
                                      className={cn(
                                        m === val
                                          ? "text-[color:var(--color-cyber-fg)]"
                                          : "text-[color:var(--color-cyber-muted)]",
                                      )}
                                    >
                                      {val === "off"
                                        ? "Désactivé"
                                        : val === "routed"
                                          ? "Routé"
                                          : "NAT"}
                                    </span>
                                  </label>
                                ),
                              )}
                            </div>
                          </td>
                          <td>
                            <select
                              value={v}
                              onChange={(e) =>
                                setTailnetDestVia((prev) => ({
                                  ...prev,
                                  [d.cidr]: e.target
                                    .value as TailnetDestinationVia,
                                }))
                              }
                              disabled={m === "off"}
                              className="cyber-input w-full px-1 py-0.5 text-[10px]"
                              title={
                                m === "off"
                                  ? "Activer le mode pour choisir la sortie"
                                  : undefined
                              }
                            >
                              <option value="tailnet">Tailscale</option>
                              <option value="wan" disabled={!wanReady}>
                                WAN{!wanReady ? " (non détecté)" : ""}
                              </option>
                              <option value="proton" disabled={!protonReady}>
                                Proton VPN
                                {!protonReady ? " (tunnel inactif)" : ""}
                              </option>
                              <option value="tor" disabled={!torReady}>
                                Tor
                                {!torReady ? " (daemon arrêté)" : ""}
                              </option>
                            </select>
                          </td>
                          <td>
                            {!d.discovered && (
                              <button
                                type="button"
                                onClick={() =>
                                  setTailnetDestState((prev) => {
                                    const next = { ...prev };
                                    delete next[d.cidr];
                                    return next;
                                  })
                                }
                                className="text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-warn)]"
                                title="Retirer cette destination personnalisée"
                              >
                                ✕
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : null;
            })()}

            {/* Import preset bundle (e.g. Netflix → 9 CIDRs in one shot). */}
            <div className="mt-2 flex items-center gap-2 text-[10px]">
              <button
                type="button"
                onClick={() => setPresetModalOpen(true)}
                className="cyber-button-ghost px-2 py-1"
                title="Importer en un clic les CIDR connus d'une application (Netflix, Plex, etc.)"
              >
                📦 Importer un préset d'application
              </button>
              {appPresetsQ.data && (
                <span className="text-[color:var(--color-cyber-muted)]">
                  {appPresetsQ.data.length} presets disponibles
                </span>
              )}
            </div>

            {/* Ajout d'une destination CIDR libre. Sert quand l'opérateur
                veut router vers une plage qui n'est pas annoncée par un
                peer Tailscale (encore) — par exemple une route qui sera
                approuvée plus tard, ou un sous-réseau accessible via une
                route statique côté kernel. Le firewall reconcile traite
                ce CIDR exactement comme les autres : forward + SNAT
                optionnel. Le routage kernel doit gérer la sortie (sinon
                le paquet est drop). */}
            <div className="mt-2 flex items-center gap-2 text-[10px]">
              <input
                type="text"
                value={newDestCidr}
                onChange={(e) => {
                  setNewDestCidr(e.target.value);
                  setNewDestError(null);
                }}
                placeholder="10.50.0.0/16"
                className="cyber-input w-44 px-2 py-1 font-mono text-[10px]"
              />
              <button
                type="button"
                onClick={() => {
                  const c = newDestCidr.trim();
                  if (!isValidCidr(c)) {
                    setNewDestError("CIDR invalide (ex: 10.50.0.0/16)");
                    return;
                  }
                  if (tailnetDestState[c]) {
                    setNewDestError("Destination déjà présente");
                    return;
                  }
                  setTailnetDestState((prev) => ({ ...prev, [c]: "snat" }));
                  setNewDestCidr("");
                  setNewDestError(null);
                }}
                className="cyber-button-ghost px-2 py-1"
              >
                ➕ Ajouter destination personnalisée
              </button>
              {newDestError && (
                <span className="text-[color:var(--color-cyber-warn)]">
                  ⚠ {newDestError}
                </span>
              )}
            </div>
            <p className="mt-1 text-[10px] text-[color:var(--color-cyber-muted)]">
              Une destination personnalisée nécessite qu'une route kernel
              existe vers <code>tailscale0</code> (ou autre, à venir en
              Phase 2 avec choix d'interface de sortie).
            </p>

            {/* === Domain-based routing rules ============================== */}
            <div className="mt-4 rounded border border-[color:var(--color-cyber-border)] bg-[color:var(--color-cyber-surface)]/30 p-2">
              <div className="cyber-label !text-[9px] mb-1 text-[color:var(--color-cyber-muted)]">
                routage par nom de domaine
              </div>
              <p className="mb-2 text-[10px] text-[color:var(--color-cyber-muted)] max-w-prose">
                Chaque règle pousse les IPs résolues pour les domaines listés
                dans un <code>ipset</code> côté Slate, puis route ces paquets
                vers la sortie choisie. Marche en temps réel — pas besoin de
                connaître les CIDR à l'avance, dnsmasq les pousse au fil des
                résolutions DNS.
              </p>

              {domainRules.length === 0 ? (
                <div className="text-[10px] text-[color:var(--color-cyber-muted)]">
                  Aucune règle. Ajouter une règle ci-dessous ou utiliser le
                  bouton « 📦 Importer un préset » qui propose aussi des
                  patterns de domaines.
                </div>
              ) : (
                <table className="cyber-table">
                  <colgroup>
                    <col className="w-24" />
                    <col />
                    <col className="w-44" />
                    <col className="w-36" />
                    <col className="w-8" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Label</th>
                      <th>Domaines</th>
                      <th>Mode</th>
                      <th>Sortie via</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {domainRules.map((rule, idx) => (
                      <tr key={`${rule.label}-${idx}`}>
                        <td className="font-mono text-[10px]">{rule.label}</td>
                        <td className="font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                          {rule.domains.join(", ")}
                        </td>
                        <td>
                          <div className="flex gap-3 text-[10px]">
                            {(["routed", "snat"] as const).map((val) => (
                              <label
                                key={val}
                                className="inline-flex items-center gap-1 cursor-pointer"
                              >
                                <input
                                  type="radio"
                                  name={`dnsrule-mode-${idx}`}
                                  checked={rule.mode === val}
                                  onChange={() =>
                                    setDomainRules((prev) =>
                                      prev.map((r, i) =>
                                        i === idx ? { ...r, mode: val } : r,
                                      ),
                                    )
                                  }
                                />
                                <span>
                                  {val === "routed" ? "Routé" : "NAT"}
                                </span>
                              </label>
                            ))}
                          </div>
                        </td>
                        <td>
                          <select
                            value={rule.via}
                            onChange={(e) =>
                              setDomainRules((prev) =>
                                prev.map((r, i) =>
                                  i === idx
                                    ? {
                                        ...r,
                                        via: e.target
                                          .value as typeof rule.via,
                                      }
                                    : r,
                                ),
                              )
                            }
                            className="cyber-input w-full px-1 py-0.5 text-[10px]"
                          >
                            <option value="tailnet">Tailscale</option>
                            <option value="wan" disabled={!wanReady}>
                              WAN{!wanReady ? " (non détecté)" : ""}
                            </option>
                            <option value="proton" disabled={!protonReady}>
                              Proton VPN
                              {!protonReady ? " (tunnel inactif)" : ""}
                            </option>
                            <option value="tor" disabled>
                              Tor (DNS routing N/A)
                            </option>
                          </select>
                        </td>
                        <td>
                          <button
                            type="button"
                            onClick={() =>
                              setDomainRules((prev) =>
                                prev.filter((_, i) => i !== idx),
                              )
                            }
                            className="text-[color:var(--color-cyber-muted)] hover:text-[color:var(--color-cyber-warn)]"
                            title="Retirer cette règle"
                          >
                            ✕
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <div className="mt-2 flex flex-wrap items-end gap-2 text-[10px]">
                <div>
                  <div className="text-[9px] uppercase tracking-wider text-[color:var(--color-cyber-muted)]">
                    label
                  </div>
                  <input
                    type="text"
                    value={newDomainRuleLabel}
                    onChange={(e) => {
                      setNewDomainRuleLabel(
                        e.target.value.toLowerCase().replace(
                          /[^a-z0-9_]/g,
                          "",
                        ),
                      );
                      setDomainRuleError(null);
                    }}
                    placeholder="netflix"
                    className="cyber-input w-28 px-2 py-1 font-mono text-[10px]"
                  />
                </div>
                <div className="flex-1 min-w-[200px]">
                  <div className="text-[9px] uppercase tracking-wider text-[color:var(--color-cyber-muted)]">
                    domaines (virgule ou espace)
                  </div>
                  <input
                    type="text"
                    value={newDomainRuleDomains}
                    onChange={(e) => {
                      setNewDomainRuleDomains(e.target.value);
                      setDomainRuleError(null);
                    }}
                    placeholder="netflix.com, nflxvideo.net"
                    className="cyber-input w-full px-2 py-1 font-mono text-[10px]"
                  />
                </div>
                <button
                  type="button"
                  onClick={() => {
                    const lbl = newDomainRuleLabel.trim();
                    const domains = newDomainRuleDomains
                      .split(/[,\s]+/)
                      .map((s) => s.trim())
                      .filter(Boolean);
                    if (!lbl) {
                      setDomainRuleError("Label requis");
                      return;
                    }
                    if (domains.length === 0) {
                      setDomainRuleError("Au moins un domaine requis");
                      return;
                    }
                    if (domainRules.some((r) => r.label === lbl)) {
                      setDomainRuleError(
                        "Une règle avec ce label existe déjà",
                      );
                      return;
                    }
                    setDomainRules((prev) => [
                      ...prev,
                      { label: lbl, domains, mode: "snat", via: "tailnet" },
                    ]);
                    setNewDomainRuleLabel("");
                    setNewDomainRuleDomains("");
                    setDomainRuleError(null);
                  }}
                  className="cyber-button-ghost px-2 py-1"
                >
                  ➕ Ajouter règle
                </button>
                {domainRuleError && (
                  <span className="text-[color:var(--color-cyber-warn)]">
                    ⚠ {domainRuleError}
                  </span>
                )}
              </div>
            </div>
          </div>
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

      {presetModalOpen && appPresetsQ.data && (
        <AppPresetImportModal
          presets={appPresetsQ.data}
          alreadyChosen={tailnetDestState}
          onImport={(preset) => {
            // Add CIDRs to the destinations list.
            setTailnetDestState((prev) => {
              const next = { ...prev };
              for (const cidr of preset.cidrs) next[cidr] = "snat";
              return next;
            });
            setTailnetDestVia((prev) => {
              const next = { ...prev };
              for (const cidr of preset.cidrs) {
                if (!next[cidr]) next[cidr] = "tailnet";
              }
              return next;
            });
            setTailnetDestLabel((prev) => {
              const next = { ...prev };
              for (const cidr of preset.cidrs) next[cidr] = preset.id;
              return next;
            });
            // Also add a matching domain rule if the preset carries
            // DNS patterns and no rule with this label exists yet.
            if (preset.domains.length > 0) {
              setDomainRules((prev) => {
                if (prev.some((r) => r.label === preset.id)) return prev;
                return [
                  ...prev,
                  {
                    label: preset.id,
                    domains: [...preset.domains],
                    mode: "snat",
                    via: "tailnet",
                  },
                ];
              });
            }
            setPresetModalOpen(false);
          }}
          onClose={() => setPresetModalOpen(false)}
        />
      )}
    </form>
  );
}

function AppPresetImportModal({
  presets,
  alreadyChosen,
  onImport,
  onClose,
}: {
  presets: AppPreset[];
  alreadyChosen: Record<string, "off" | "routed" | "snat">;
  onImport: (preset: AppPreset) => void;
  onClose: () => void;
}) {
  // Portal to document.body so the modal escapes the parent form's
  // `clip-path` containing block (otherwise `position: fixed` would be
  // clipped to the form rectangle instead of the viewport).
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="cyber-card cyber-card-accent w-full max-w-2xl p-5"
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="cyber-display cyber-glow text-lg">
            IMPORTER UN PRESET D'APPLICATION
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="border border-transparent p-1.5 text-[color:var(--color-cyber-muted)] hover:border-[color:var(--color-cyber-accent)] hover:text-[color:var(--color-cyber-accent)]"
          >
            ✕
          </button>
        </div>
        <p className="mb-3 text-[11px] text-[color:var(--color-cyber-muted)] max-w-prose">
          Les CIDR connus de l'application sélectionnée seront ajoutés à la
          liste des destinations atteignables (mode SNAT, via Tailscale par
          défaut — modifiable ensuite ligne par ligne). Snapshot du
          catalogue ; pour Netflix par exemple, les ranges de l'AS 2906
          (Open Connect) sont preloadés. Toujours vérifier après import si
          un nouveau range a été publié récemment.
        </p>
        <table className="cyber-table">
          <colgroup>
            <col className="w-32" />
            <col />
            <col className="w-20" />
            <col className="w-24" />
          </colgroup>
          <thead>
            <tr>
              <th>Application</th>
              <th>Description</th>
              <th>CIDRs</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {presets.map((p) => {
              const overlap = p.cidrs.filter((c) => c in alreadyChosen).length;
              return (
                <tr key={p.id}>
                  <td className="font-mono text-[11px]">{p.name}</td>
                  <td className="text-[10px] text-[color:var(--color-cyber-muted)]">
                    {p.description}
                  </td>
                  <td className="font-mono text-[10px] text-[color:var(--color-cyber-muted)]">
                    {p.cidrs.length}
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => onImport(p)}
                      className="cyber-button-ghost px-2 py-0.5 text-[10px]"
                    >
                      {overlap > 0
                        ? `Importer (+${p.cidrs.length - overlap})`
                        : "Importer"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>,
    document.body,
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
