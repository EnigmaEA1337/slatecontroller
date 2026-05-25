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
