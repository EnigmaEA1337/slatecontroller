import axios, { AxiosError } from "axios";
import { clearToken, getToken } from "@/lib/auth-storage";

// Relative by default so requests go to the same origin that served the UI.
// Vite dev (vite.config.ts) proxies `/api` to the backend; production behind
// Traefik routes `/api` to the controller. Same-origin = no CORS, works for
// localhost / LAN / Tailscale / Traefik domain alike.
// Override only if you intentionally want to hit a remote backend from a
// locally-served UI (e.g. testing).
const baseURL = import.meta.env.VITE_API_URL ?? "";

export const api = axios.create({
  baseURL,
  timeout: 15_000,
});

// Attach Bearer token if present.
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On 401, drop the token and bounce to /login (full reload so React state
// resets cleanly). We avoid this for the /auth/login endpoint itself so the
// caller can render a "wrong password" message instead of redirecting.
api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      const url = error.config?.url ?? "";
      if (!url.includes("/auth/login")) {
        clearToken();
        if (window.location.pathname !== "/login") {
          window.location.href = "/login";
        }
      }
    }
    return Promise.reject(error);
  },
);
