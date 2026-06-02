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
