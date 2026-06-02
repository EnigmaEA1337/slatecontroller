import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: 5173,
    // Vite 5.4+ ships a Host-header allowlist (CVE-2024-23331 mitigation
    // against DNS rebinding when running the dev server). With the
    // Tailscale sidecar architecture we get hit by requests bearing
    // `<host>.<tailnet>.ts.net` as the Host header, which Vite rejects
    // with 403. Allow the `.ts.net` parent so any tailnet hostname works
    // (including future renames of TS_HOSTNAME). LAN access via raw IP
    // doesn't need an entry — it's covered by the default `localhost`
    // and the IP-literal allowance Vite ships with.
    allowedHosts: [".ts.net"],
    // Same-origin API proxy. Lets the browser hit `/api/...` on whatever
    // host serves the UI (localhost / LAN / Tailscale 100.x.x.x / Traefik
    // domain) without CORS preflights or runtime baseURL detection.
    // VITE_BACKEND_URL is read from the dev env (compose injects it),
    // falls back to the local backend dev server.
    proxy: {
      "/api": {
        target: process.env.VITE_BACKEND_URL ?? "http://localhost:8000",
        changeOrigin: true,
        secure: false,
      },
    },
  },
});
