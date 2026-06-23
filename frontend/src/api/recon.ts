// API client for /api/recon.

import { api } from "./client";

export type ReconStatus = "running" | "done" | "failed" | "cancelled";
export type ReconFamily = "wan" | "lan" | "guest" | "other";

export interface ReconInterface {
  name: string;
  ipv4_cidr: string;
  family: ReconFamily;
  host_count: number;
  scannable: boolean;
  gateway: string;
}

export interface ReconLaunchRequest {
  interfaces: string[];
  do_arp: boolean;
  do_ping: boolean;
  do_tcp: boolean;
  do_banner: boolean;
}

export interface ReconScanScope {
  interfaces: string[];
  do_arp: boolean;
  do_ping: boolean;
  do_tcp: boolean;
  do_banner: boolean;
}

export interface ReconScanSummary {
  id: number;
  status: ReconStatus;
  progress: string;
  error: string;
  host_count: number;
  port_count: number;
  started_at: string;
  finished_at: string | null;
  scope: ReconScanScope | Record<string, unknown>;
}

export interface ReconHost {
  interface: string;
  ip: string;
  mac: string;
  vendor: string;
  hostname: string;
  source: string; // arp / ping / both / meta
  is_gateway: boolean;
  is_self: boolean;
}

export interface ReconPort {
  ip: string;
  port: number;
  state: string;
  banner: string;
  service: string;
}

export interface ReconScanDetail extends ReconScanSummary {
  hosts: ReconHost[];
  ports: ReconPort[];
}

export async function listReconInterfaces(): Promise<ReconInterface[]> {
  const { data } = await api.get<ReconInterface[]>("/recon/interfaces");
  return data;
}

export async function launchReconScan(
  body: ReconLaunchRequest,
): Promise<ReconScanSummary> {
  const { data } = await api.post<ReconScanSummary>("/recon/scans", body);
  return data;
}

export async function listReconScans(): Promise<ReconScanSummary[]> {
  const { data } = await api.get<ReconScanSummary[]>("/recon/scans");
  return data;
}

export async function getReconScan(id: number): Promise<ReconScanDetail> {
  const { data } = await api.get<ReconScanDetail>(`/recon/scans/${id}`);
  return data;
}

export async function cancelReconScan(id: number): Promise<ReconScanSummary> {
  const { data } = await api.post<ReconScanSummary>(
    `/recon/scans/${id}/cancel`,
  );
  return data;
}

export async function deleteReconScan(id: number): Promise<void> {
  await api.delete(`/recon/scans/${id}`);
}
