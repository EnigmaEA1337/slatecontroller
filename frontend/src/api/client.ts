import axios, { AxiosError } from "axios";
import { clearToken, getToken } from "@/lib/auth-storage";
import { getActiveDevice } from "@/lib/device-context";

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

// Attach `?device=<slug>` for the routes that target a specific device,
// when the user has selected one in the DevicePicker. Routes that are
// inherently device-scoped by URL path (e.g. `/api/devices/<slug>/...`)
// don't need this and we skip them to avoid double-scoping confusion.
// Auth + devices-list are global — they should never get the query
// param. The backend treats `?device=` as optional anyway, so a stray
// one is harmless, but cleaner to keep requests minimal.
const NO_DEVICE_PARAM_PREFIXES = [
  "/api/auth/",
  "/api/devices", // covers /api/devices and /api/devices/<slug>/...
];

function shouldAttachDeviceParam(url: string | undefined): boolean {
  if (!url) return false;
  return !NO_DEVICE_PARAM_PREFIXES.some((p) => url.startsWith(p));
}

api.interceptors.request.use((config) => {
  const slug = getActiveDevice();
  if (slug && shouldAttachDeviceParam(config.url)) {
    config.params = { ...(config.params ?? {}), device: slug };
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
