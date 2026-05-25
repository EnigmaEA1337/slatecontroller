import { api } from "./client";
import type { ProtonAuthStatus } from "@/types/proton";

export async function getProtonStatus(): Promise<ProtonAuthStatus> {
  const { data } = await api.get<ProtonAuthStatus>("/api/vpn/proton/auth/status");
  return data;
}

export async function protonLogin(
  username: string,
  password: string,
): Promise<ProtonAuthStatus> {
  const { data } = await api.post<ProtonAuthStatus>(
    "/api/vpn/proton/auth/login",
    { username, password },
  );
  return data;
}

export async function protonSubmit2FA(code: string): Promise<ProtonAuthStatus> {
  const { data } = await api.post<ProtonAuthStatus>(
    "/api/vpn/proton/auth/2fa",
    { code },
  );
  return data;
}

export async function protonLogout(): Promise<void> {
  await api.post("/api/vpn/proton/auth/logout");
}
