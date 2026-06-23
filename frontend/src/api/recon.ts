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
  /** Subnet actually swept (== ipv4_cidr, OR a /24 around the Slate when the
   *  declared subnet was too wide to ping in reasonable time). */
  scan_cidr: string;
  /** True iff scan_cidr was clamped down from a wider ipv4_cidr. */
  scan_clamped: boolean;
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
  const { data } = await api.get<ReconInterface[]>("/api/recon/interfaces");
  return data;
}

export async function launchReconScan(
  body: ReconLaunchRequest,
): Promise<ReconScanSummary> {
  const { data } = await api.post<ReconScanSummary>("/api/recon/scans", body);
  return data;
}

export async function listReconScans(): Promise<ReconScanSummary[]> {
  const { data } = await api.get<ReconScanSummary[]>("/api/recon/scans");
  return data;
}

export async function getReconScan(id: number): Promise<ReconScanDetail> {
  const { data } = await api.get<ReconScanDetail>(`/api/recon/scans/${id}`);
  return data;
}

export async function cancelReconScan(id: number): Promise<ReconScanSummary> {
  const { data } = await api.post<ReconScanSummary>(
    `/api/recon/scans/${id}/cancel`,
  );
  return data;
}

export async function deleteReconScan(id: number): Promise<void> {
  await api.delete(`/api/recon/scans/${id}`);
}


// ---------------------------- tools ---------------------------- //

export interface ReconToolStatus {
  has_nmap: boolean;
  has_arp_scan: boolean;
  has_gl_arp_scan: boolean;
  nmap_version: string;
  arp_scan_version: string;
  overlay_free_mb: number;
  fully_installed: boolean;
}

export interface ReconInstallReport {
  ok: boolean;
  log: string;
  status: ReconToolStatus;
}

export async function getReconTools(): Promise<ReconToolStatus> {
  const { data } = await api.get<ReconToolStatus>("/api/recon/tools");
  return data;
}

export async function installReconTools(): Promise<ReconInstallReport> {
  const { data } = await api.post<ReconInstallReport>(
    "/api/recon/tools/install",
  );
  return data;
}
