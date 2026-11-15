import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Development only. In production the API serves the built `dist/` itself
// (same origin), so this dev server and its proxy are never exposed.
const API_DEV_TARGET = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API_DEV_TARGET, changeOrigin: true },
      "/health": { target: API_DEV_TARGET, changeOrigin: true },
    },
  },
});
