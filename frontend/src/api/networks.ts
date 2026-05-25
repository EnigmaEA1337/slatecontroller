import { api } from "./client";
import type { NetworkCreate, NetworkPublic, NetworkWrite } from "@/types/network";
import type { NetworkDiag } from "@/types/network-diag";

export async function listNetworks(): Promise<NetworkPublic[]> {
  const { data } = await api.get<NetworkPublic[]>("/api/networks");
  return data;
}

export async function getNetwork(slug: string): Promise<NetworkPublic> {
  const { data } = await api.get<NetworkPublic>(
    `/api/networks/${encodeURIComponent(slug)}`,
  );
  return data;
}

export async function createNetwork(body: NetworkCreate): Promise<NetworkPublic> {
  const { data } = await api.post<NetworkPublic>("/api/networks", body);
  return data;
}

export async function updateNetwork(
  slug: string,
  body: NetworkWrite,
): Promise<NetworkPublic> {
  const { data } = await api.put<NetworkPublic>(
    `/api/networks/${encodeURIComponent(slug)}`,
    body,
  );
  return data;
}

export async function deleteNetwork(slug: string): Promise<void> {
  await api.delete(`/api/networks/${encodeURIComponent(slug)}`);
}

export async function getNetworkDiag(): Promise<NetworkDiag> {
  // Each probe is ~3-5s and the lock-serialised series adds up to ~25-30s.
  // Bump the client timeout well above that.
  const { data } = await api.get<NetworkDiag>("/api/networks/diag", {
    timeout: 60_000,
  });
  return data;
}
