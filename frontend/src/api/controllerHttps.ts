import { api } from "./client";

export interface ControllerHttpsRoute {
  path: string;
  target: string;
}

export interface ControllerHttpsCert {
  issuer: string | null;
  not_after: string | null;
  days_remaining: number | null;
}

export interface ControllerHttpsState {
  cli_available: boolean;
  daemon_reachable: boolean;
  operator_set: boolean;
  tailnet_hostname: string | null;
  tailnet_name: string | null;
  tailscale_ips: string[];
  https_enabled: boolean;
  routes: ControllerHttpsRoute[];
  cert: ControllerHttpsCert | null;
  public_url: string | null;
  raw_error: string | null;
  /** False = the user hasn't enabled HTTPS in the tailnet admin
   *  (https://login.tailscale.com/admin/dns → Enable HTTPS). Without
   *  this, `tailscale cert` returns 500 and Serve can't issue certs. */
  feature_https_enabled_in_admin: boolean | null;
}

export async function getControllerHttpsState(): Promise<ControllerHttpsState> {
  // Cert read can call out to tailscaled which has its own latency.
  const { data } = await api.get<ControllerHttpsState>(
    "/api/settings/controller-https",
    { timeout: 25_000 },
  );
  return data;
}

export interface ControllerHttpsWriteResponse {
  ok: boolean;
  message: string;
  operator_hint?: boolean;
}

export async function enableControllerHttps(): Promise<ControllerHttpsWriteResponse> {
  const { data } = await api.post<ControllerHttpsWriteResponse>(
    "/api/settings/controller-https/enable",
    null,
    { timeout: 30_000 },
  );
  return data;
}

export async function disableControllerHttps(): Promise<ControllerHttpsWriteResponse> {
  const { data } = await api.post<ControllerHttpsWriteResponse>(
    "/api/settings/controller-https/disable",
    null,
    { timeout: 15_000 },
  );
  return data;
}
