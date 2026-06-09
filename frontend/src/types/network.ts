/**
 * Isolation model (see backend NetworkRow docstring for the canonical
 * source of truth) :
 *
 *   intra_bridge_isolation  L2 — ports of the SAME bridge cloisonnés
 *                           (rare ; achieved via bridge port_isolation
 *                           or ebtables). Most setups use separate
 *                           bridges instead.
 *   reach_internet          L3 — forwarding to wan zone allowed.
 *                           Default ON. False = no internet for clients.
 *   reachable_networks      L3 — list of OTHER network slugs this one
 *                           can route to (besides wan). Empty = full
 *                           isolation from every other subnet.
 *
 * Admin / management plane is split per service (was a single
 * `admin_access` flag before — see migration b2c4d68e90f1) :
 *
 *   services_access         input — DHCP, DNS local (dnsmasq), ICMP.
 *                           Default ON. False = clients can't even
 *                           get an IP through the Slate.
 *   admin_ui_access         input — LuCI + GL.iNet web UI (TCP 80/443).
 *                           Default OFF — only trusted networks.
 *   ssh_access              input — SSH / dropbear (TCP 22).
 *                           Default OFF — explicit opt-in.
 *
 * Separate concern : client_isolation on the WiFi SSID (intra-SSID L2)
 * lives on the WifiSsid model, NOT here.
 */
export interface NetworkPublic {
  slug: string;
  display_name: string;
  bridge_name: string;
  subnet_cidr: string;
  gateway_ip: string;
  dhcp_enabled: boolean;
  vlan_tag: number | null;
  notes: string;
  ipv6_enabled: boolean;
  ipv6_subnet_cidr: string;

  intra_bridge_isolation: boolean;
  reach_internet: boolean;
  reachable_networks: string[];

  services_access: boolean;
  admin_ui_access: boolean;
  ssh_access: boolean;

  // Tailnet subnet routing. When true the Slate advertises this
  // network's CIDR(s) as `--advertise-routes=...` so tailnet peers can
  // reach hosts in this subnet via the Slate's tailscale0.
  expose_to_tailnet: boolean;

  // Per-destination reverse routing : which tailnet subnets the clients
  // of THIS network are allowed to reach, with the NAT mode applied
  // per entry. Empty = no reverse routing.
  tailnet_destinations: TailnetDestination[];

  // Domain-based routing rules — each rule routes every IP DNS resolves
  // for `domains` out through `via`. Backed by dnsmasq+ipset+fwmark on
  // the Slate.
  domain_routing_rules: DomainRoutingRule[];

  // Per-network Tor routing (see backend NetworkRow docstring).
  tor_route_mode: "off" | "transparent" | "socks_only";
  tor_dns_over_tor: boolean;
  tor_kill_switch: boolean;

  created_at: string;
  updated_at: string;
}

/** Egress path for one destination CIDR.
 *  - "tailnet" : route via tailscale0 (default).
 *  - "wan"     : route via the WAN interface (eth0/apcli0/etc).
 *  - "proton"  : Proton VPN tunnel (NOT YET IMPLEMENTED in the backend).
 *  - "tor"     : Tor TransPort      (NOT YET IMPLEMENTED in the backend).
 */
export type TailnetDestinationVia = "tailnet" | "wan" | "proton" | "tor";

export interface TailnetDestination {
  cidr: string;
  /** "routed" = real client IP visible to peer ; "snat" = NAT to egress IP. */
  mode: "routed" | "snat";
  /** Egress path. Default: "tailnet". */
  via: TailnetDestinationVia;
  /** Free-form label used by the UI to group destinations by source
   *  (e.g. "netflix"). Empty string for manually entered ones. */
  label?: string;
}

export interface DomainRoutingRule {
  /** Short identifier — used as the ipset name suffix. Lowercase, no spaces. */
  label: string;
  /** DNS names dnsmasq watches. Prefix `.example.com` matches all subdomains. */
  domains: string[];
  mode: "routed" | "snat";
  via: TailnetDestinationVia;
}

export interface NetworkWrite {
  display_name: string;
  bridge_name: string;
  subnet_cidr: string;
  gateway_ip: string;
  dhcp_enabled: boolean;
  vlan_tag: number | null;
  notes: string;
  ipv6_enabled: boolean;
  ipv6_subnet_cidr: string;
  intra_bridge_isolation: boolean;
  reach_internet: boolean;
  reachable_networks: string[];
  services_access: boolean;
  admin_ui_access: boolean;
  ssh_access: boolean;
  expose_to_tailnet: boolean;
  tailnet_destinations: TailnetDestination[];
  domain_routing_rules: DomainRoutingRule[];
  tor_route_mode: "off" | "transparent" | "socks_only";
  tor_dns_over_tor: boolean;
  tor_kill_switch: boolean;
}

export interface NetworkCreate extends NetworkWrite {
  slug: string;
}
