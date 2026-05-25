import { api } from "./client";
import type { TokenResponse, User } from "@/types/auth";

export async function login(username: string, password: string): Promise<TokenResponse> {
  // OAuth2 password flow expects form-encoded body.
  const body = new URLSearchParams({ username, password });
  const { data } = await api.post<TokenResponse>("/api/auth/login", body, {
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  return data;
}

export async function logout(): Promise<void> {
  await api.post("/api/auth/logout");
}

export async function getMe(): Promise<User> {
  const { data } = await api.get<User>("/api/auth/me");
  return data;
}
