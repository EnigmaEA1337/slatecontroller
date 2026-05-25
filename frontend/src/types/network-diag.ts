export interface DiagAddress {
  family: string; // "inet" | "inet6"
  local: string;
  prefixlen: number;
  scope: string | null;
  broadcast: string | null;
  label: string | null;
}

export interface DiagCounters {
  rx_bytes: number;
  rx_packets: number;
  rx_errs: number;
  rx_drop: number;
  tx_bytes: number;
  tx_packets: number;
  tx_errs: number;
  tx_drop: number;
}

export interface DiagInterface {
  name: string;
  index: number;
  operstate: string;
  flags: string[];
  mtu: number;
  mac: string;
  master: string | null;
  link_type: string;
  addresses: DiagAddress[];
  counters: DiagCounters | null;
}

export interface DiagRoute {
  dst: string;
  gateway: string | null;
  dev: string;
  protocol: string | null;
  scope: string | null;
  src: string | null;
  metric: number | null;
  type: string | null;
  flags: string[];
  // Routing table name (main / local / 52 / 1001-… for multiwan).
  // Defaults to "main" server-side so this is always populated.
  table: string;
}

export interface DiagRule {
  priority: number | null;
  src: string | null;
  dst: string | null;
  iif: string | null;
  oif: string | null;
  fwmark: string | null;
  table: string;
  action: string | null;
  suppress_prefixlength: number | null;
}

export interface DiagNeighbour {
  ip: string;
  dev: string;
  lladdr: string | null;
  state: string | null;
  router: boolean;
}

export interface DiagLogicalInterface {
  interface: string;
  up: boolean;
  proto: string;
  l3_device: string | null;
  device: string | null;
  uptime: number;
  "ipv4-address": { address: string; mask: number }[];
  "ipv6-address": unknown[];
  "dns-server": string[];
  // and many more — kept loose
  [key: string]: unknown;
}

export interface NetworkDiag {
  interfaces: DiagInterface[];
  routes_v4: DiagRoute[];
  routes_v6: DiagRoute[];
  rules: DiagRule[];
  neighbours: DiagNeighbour[];
  logical_interfaces: DiagLogicalInterface[];
}
