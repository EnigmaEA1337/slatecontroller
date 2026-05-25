/**
 * 3-dimension isolation model (see backend NetworkRow docstring) :
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
 *   admin_access            input policy — clients can reach the Slate
 *                           itself (DHCP/DNS/UI). Default ON ; turning
 *                           OFF makes the network unusable for most.
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
  admin_access: boolean;

  created_at: string;
  updated_at: string;
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
  admin_access: boolean;
}

export interface NetworkCreate extends NetworkWrite {
  slug: string;
}
