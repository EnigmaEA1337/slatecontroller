export interface NetworkPublic {
  slug: string;
  display_name: string;
  bridge_name: string;
  subnet_cidr: string;
  gateway_ip: string;
  dhcp_enabled: boolean;
  isolated_from_lan: boolean;
  vlan_tag: number | null;
  is_builtin: boolean;
  notes: string;
  ipv6_enabled: boolean;
  ipv6_subnet_cidr: string;
  created_at: string;
  updated_at: string;
}

export interface NetworkWrite {
  display_name: string;
  bridge_name: string;
  subnet_cidr: string;
  gateway_ip: string;
  dhcp_enabled: boolean;
  isolated_from_lan: boolean;
  vlan_tag: number | null;
  notes: string;
  ipv6_enabled: boolean;
  ipv6_subnet_cidr: string;
}

export interface NetworkCreate extends NetworkWrite {
  slug: string;
}
