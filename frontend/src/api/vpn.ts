import { api } from "./client";
import type { VPNConfigPublic, VPNConfigUploadResponse, VpnProvider } from "@/types/vpn";

export async function listVPNConfigs(): Promise<VPNConfigPublic[]> {
  const { data } = await api.get<VPNConfigPublic[]>("/api/vpn/configs");
  return data;
}

export async function uploadVPNConfig(
  file: File,
  name: string,
  provider: VpnProvider = "proton",
): Promise<VPNConfigUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("name", name);
  form.append("provider", provider);
  const { data } = await api.post<VPNConfigUploadResponse>(
    "/api/vpn/configs",
    form,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return data;
}

export async function deleteVPNConfig(name: string): Promise<void> {
  await api.delete(`/api/vpn/configs/${encodeURIComponent(name)}`);
}
