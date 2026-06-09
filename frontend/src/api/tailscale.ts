import { api } from "./client";
import type {
  TailscaleAuditReport,
  TailscaleConfigInput,
  TailscaleConfigSummary,
  TailscaleConnectResponse,
  TailscaleHAConfigPatch,
  TailscaleHAState,
  TailscaleStatus,
} from "@/types/tailscale";

export async function getTailscaleStatus(): Promise<TailscaleStatus> {
  // Underlying SSH+CLI can take a few seconds on cold start; bump timeout.
  const { data } = await api.get<TailscaleStatus>("/api/tailscale/status", {
    timeout: 30_000,
  });
  return data;
}

export async function getTailscaleConfig(): Promise<TailscaleConfigSummary> {
  const { data } = await api.get<TailscaleConfigSummary>(
    "/api/tailscale/config",
  );
  return data;
}

export async function connectTailscale(
  body: TailscaleConfigInput,
): Promise<TailscaleConnectResponse> {
  // `tailscale up` can wait up to its own timeout — give a generous window.
  const { data } = await api.post<TailscaleConnectResponse>(
    "/api/tailscale/connect",
    body,
    { timeout: 60_000 },
  );
  return data;
}

export async function disconnectTailscale(): Promise<void> {
  await api.post("/api/tailscale/disconnect", undefined, { timeout: 20_000 });
}

// --- Subnet route sync (catalog → Slate advertise-routes) -----------

export interface SyncRoutesPreview {
  expected: string[];
  current_advertised: string[];
  /** null when no PAT is configured — UI shows "approval unknown". */
  current_approved: string[] | null;
  to_add: string[];
  to_remove: string[];
  /** Routes expected but not yet approved in the tailnet admin. null
   *  when no PAT is configured. */
  not_yet_approved: string[] | null;
  in_sync: boolean;
}

export interface SyncRoutesApprovalReport {
  attempted: boolean;
  reason?: string;
  approved?: string[];
  error?: string;
}

export interface SyncRoutesResult {
  ok: boolean;
  expected: string[];
  applied: string[];
  approval: SyncRoutesApprovalReport;
  status: TailscaleStatus;
}

export async function previewSyncTailscaleRoutes(): Promise<SyncRoutesPreview> {
  const { data } = await api.get<SyncRoutesPreview>(
    "/api/tailscale/sync-routes/preview",
    { timeout: 30_000 },
  );
  return data;
}

export async function syncTailscaleRoutes(): Promise<SyncRoutesResult> {
  const { data } = await api.post<SyncRoutesResult>(
    "/api/tailscale/sync-routes",
    undefined,
    { timeout: 60_000 },
  );
  return data;
}

// --- Subnet routing inverse — LAN clients → tailnet peers ----------

/** "routed" = pas de NAT, le pair distant voit l'IP réelle du client (et
 *  doit donc avoir `--accept-routes` + la route approuvée dans le tailnet).
 *  "snat" = SNAT vers l'IP Tailscale du Slate, marche partout sans config
 *  côté pair distant mais perd la visibilité du client. */
export type ReverseRoutingMode = "routed" | "snat";

export interface TailnetDestinationCandidate {
  cidr: string;
  /** Hosts qui annoncent ce subnet dans le tailnet. Au moins un élément. */
  peers: string[];
}

export interface TailnetForwardingActivePair {
  zone: string;
  dest_cidr: string;
}

export interface ReverseRoutingSubnet {
  slug: string;
  zone: string;
  iface: string;
  cidr: string;
  ipaddr: string;
}

export interface ReverseRoutingState {
  tailscale_zone_exists: boolean;
  tailscale_self_ip: string | null;
  /** WAN egress interface auto-detected from the default route. */
  wan_iface: string | null;
  /** Proton VPN tunnel interface if one is configured + up. null = not ready. */
  proton_iface: string | null;
  /** True when the Tor daemon is running and TransPort is listening. */
  tor_active: boolean;
  subnets: ReverseRoutingSubnet[];
  active_fwd: TailnetForwardingActivePair[];
  active_snat: TailnetForwardingActivePair[];
  active_tor: TailnetForwardingActivePair[];
}

export interface ReverseRoutingReconcileReport {
  ok: boolean;
  applied_rules: number;
  active_fwd: [string, string][];
  active_snat: [string, string][];
  reload_output?: string;
}

export async function getReverseRouting(): Promise<ReverseRoutingState> {
  const { data } = await api.get<ReverseRoutingState>(
    "/api/tailscale/forwarding",
    { timeout: 30_000 },
  );
  return data;
}

export interface AppPreset {
  id: string;
  name: string;
  description: string;
  cidrs: string[];
  /** DNS names matching this app — fed to dnsmasq for the DNS-routing
   *  mode. May be empty for very loose ASN-only presets. */
  domains: string[];
}

export interface DnsRoutingReconcileReport {
  ok: boolean;
  applied_rules: number;
  ipsets: string[];
  destroyed_orphans: string[];
  reload_output?: string;
}

export async function reconcileDnsRouting(): Promise<DnsRoutingReconcileReport> {
  const { data } = await api.post<DnsRoutingReconcileReport>(
    "/api/tailscale/dns-routing/reconcile",
    undefined,
    { timeout: 60_000 },
  );
  return data;
}

export async function listAppPresets(): Promise<AppPreset[]> {
  const { data } = await api.get<{ presets: AppPreset[] }>(
    "/api/tailscale/app-presets",
    { timeout: 10_000 },
  );
  return data.presets;
}

export async function listTailnetDestinationCandidates(): Promise<
  TailnetDestinationCandidate[]
> {
  const { data } = await api.get<{
    destinations: TailnetDestinationCandidate[];
  }>("/api/tailscale/destinations", { timeout: 30_000 });
  return data.destinations;
}

/** Trigger a reconciliation : the backend walks every Network row,
 *  reads its `tailnet_destinations` list, and rebuilds the live
 *  iptables rules to match. Idempotent. */
export async function reconcileReverseRouting(): Promise<
  ReverseRoutingReconcileReport
> {
  const { data } = await api.post<ReverseRoutingReconcileReport>(
    "/api/tailscale/forwarding/reconcile",
    undefined,
    { timeout: 60_000 },
  );
  return data;
}

export async function logoutTailscale(): Promise<void> {
  await api.post("/api/tailscale/logout", undefined, { timeout: 20_000 });
}

export interface PingResult {
  ok: boolean;
  output: string;
  target: string;
  mode: string;
}

export async function pingTailscale(
  target: string,
  mode: "icmp" | "tailscale",
  count = 3,
): Promise<PingResult> {
  const { data } = await api.post<PingResult>(
    "/api/tailscale/ping",
    { target, mode, count },
    { timeout: 30_000 },
  );
  return data;
}

export interface TracerouteResult {
  ok: boolean;
  output: string;
  target: string;
  max_hops: number;
}

export interface AdminPatStatus {
  configured: boolean;
  tailnet: string | null;
  last_verified_at: string | null;
}

export async function getAdminPatStatus(): Promise<AdminPatStatus> {
  const { data } = await api.get<AdminPatStatus>("/api/tailscale/admin/pat");
  return data;
}

export async function setAdminPat(
  pat: string,
  tailnet?: string,
): Promise<{ configured: boolean; tailnet: string; device_count: number }> {
  // Validation hits the Tailscale API (~1s); slight headroom.
  const { data } = await api.post(
    "/api/tailscale/admin/pat",
    { pat, tailnet: tailnet || null },
    { timeout: 20_000 },
  );
  return data;
}

export async function deleteAdminPat(): Promise<void> {
  await api.delete("/api/tailscale/admin/pat");
}

export async function getTailscaleHA(): Promise<TailscaleHAState> {
  const { data } = await api.get<TailscaleHAState>("/api/tailscale/ha");
  return data;
}

export async function updateTailscaleHA(
  patch: TailscaleHAConfigPatch,
): Promise<TailscaleHAState> {
  const { data } = await api.post<TailscaleHAState>(
    "/api/tailscale/ha",
    patch,
  );
  return data;
}

export async function auditTailscale(): Promise<TailscaleAuditReport> {
  // Audit fan-outs ~6 SSH probes in parallel — keep timeout generous.
  const { data } = await api.get<TailscaleAuditReport>(
    "/api/tailscale/audit",
    { timeout: 60_000 },
  );
  return data;
}

export async function fixTailscaleAuditFinding(
  findingId: string,
): Promise<{ ok: boolean; finding_id: string; message: string }> {
  const { data } = await api.post(
    "/api/tailscale/audit/fix",
    null,
    { params: { finding_id: findingId }, timeout: 30_000 },
  );
  return data;
}

export async function tracerouteTailscale(
  target: string,
  max_hops = 15,
): Promise<TracerouteResult> {
  const { data } = await api.post<TracerouteResult>(
    "/api/tailscale/traceroute",
    { target, max_hops },
    // Worst case ~max_hops * 2s + slack
    { timeout: Math.max(60_000, max_hops * 2_500) },
  );
  return data;
}
