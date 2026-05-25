/**
 * Client for the slate-controller local agent endpoints.
 *
 * The agent is a set of shell scripts that live on the Slate at
 * /etc/slate-controller/ + /usr/local/bin/slate-ctrl. Once deployed, the
 * Slate can apply profiles by itself — even when the controller is offline.
 * These functions wrap the controller's `/api/agent/*` endpoints which
 * orchestrate the deploy / sync / apply over SSH.
 */
import { api } from "@/api/client";

export interface AgentStatus {
  installed: boolean;
  version: string | null;
  remote_profiles: string[];
  active: string | null;
}

export interface AgentDeployResult {
  ok: boolean;
  pushed: string[];
  errors: string[];
}

export interface AgentSyncResult {
  ok: boolean;
  profiles: { ok: boolean; pushed: string[]; errors: string[] };
  screens:  { ok: boolean; pushed: string[]; errors: string[] };
}

export interface AgentApplyResult {
  ok: boolean;
  name: string;
  output: string;
}

export async function getAgentStatus(): Promise<AgentStatus> {
  const { data } = await api.get<AgentStatus>("/api/agent/status");
  return data;
}

export async function deployAgent(): Promise<AgentDeployResult> {
  // Deploy involves a handful of small SSH put_bytes calls.
  const { data } = await api.post<AgentDeployResult>(
    "/api/agent/deploy", undefined, { timeout: 30_000 },
  );
  return data;
}

export async function syncAgentProfiles(): Promise<AgentSyncResult> {
  // One SSH put per profile JSON → ~5 small calls.
  const { data } = await api.post<AgentSyncResult>(
    "/api/agent/sync", undefined, { timeout: 30_000 },
  );
  return data;
}

export async function applyAgentProfile(name: string): Promise<AgentApplyResult> {
  // slate-ctrl apply runs through each handler; budget 60s.
  const { data } = await api.post<AgentApplyResult>(
    `/api/agent/apply/${encodeURIComponent(name)}`, undefined, { timeout: 60_000 },
  );
  return data;
}
