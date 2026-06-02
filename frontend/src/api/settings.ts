import { api } from "./client";
import type {
  DeployRequest,
  DeployResponse,
  SSHKeypairStatus,
} from "@/types/settings";

export async function getSshKeypairStatus(): Promise<SSHKeypairStatus> {
  const { data } = await api.get<SSHKeypairStatus>("/api/settings/ssh-keypair");
  return data;
}

export async function generateSshKeypair(): Promise<SSHKeypairStatus> {
  const { data } = await api.post<SSHKeypairStatus>(
    "/api/settings/ssh-keypair/generate",
  );
  return data;
}

export async function deploySshKeypair(
  body: DeployRequest,
): Promise<DeployResponse> {
  const { data } = await api.post<DeployResponse>(
    "/api/settings/ssh-keypair/deploy",
    body,
  );
  return data;
}

export async function revokeSshKeypair(): Promise<void> {
  await api.delete("/api/settings/ssh-keypair");
}

export async function exportSshPrivateKey(): Promise<Blob> {
  const { data } = await api.get<Blob>("/api/settings/ssh-keypair/private-key", {
    responseType: "blob",
  });
  return data;
}

// ---- Controller URLs (Slate→Controller callback hooks) ----

export interface ControllerUrls {
  tailscale_url: string;
  lan_url: string;
  preferred: "tailscale" | "lan";
}

export async function getControllerUrls(): Promise<ControllerUrls> {
  const { data } = await api.get<ControllerUrls>("/api/settings/controller-urls");
  return data;
}

export async function updateControllerUrls(
  patch: Partial<ControllerUrls>,
): Promise<ControllerUrls> {
  const { data } = await api.post<ControllerUrls>(
    "/api/settings/controller-urls",
    patch,
  );
  return data;
}

// ---- Slate communication preferences ----

export interface SlateComms {
  show_screen_messages: boolean;
}

export async function getSlateComms(): Promise<SlateComms> {
  const { data } = await api.get<SlateComms>("/api/settings/slate-comms");
  return data;
}

export async function updateSlateComms(
  patch: Partial<SlateComms>,
): Promise<SlateComms> {
  const { data } = await api.post<SlateComms>("/api/settings/slate-comms", patch);
  return data;
}

// ---- Tailnet admin IPs (drives Profile.tailscale.admin_only firewall) ----

export interface TailnetAdminConfig {
  admin_ips: string[];
}

export async function getTailnetAdminIps(): Promise<TailnetAdminConfig> {
  const { data } = await api.get<TailnetAdminConfig>(
    "/api/settings/tailnet-admin-ips",
  );
  return data;
}

export async function updateTailnetAdminIps(
  admin_ips: string[],
): Promise<TailnetAdminConfig> {
  const { data } = await api.put<TailnetAdminConfig>(
    "/api/settings/tailnet-admin-ips",
    { admin_ips },
  );
  return data;
}
