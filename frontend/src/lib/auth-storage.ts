// Minimal token storage. localStorage is fine for an admin-only app behind
// Tailscale/Traefik. If we ever expose to untrusted browsers we should switch
// to httpOnly cookies (and then login becomes a session-cookie endpoint).

const KEY = "slate.token";

export function getToken(): string | null {
  return localStorage.getItem(KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(KEY);
}
