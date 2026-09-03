import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the package so FastAPI serves the app with no extra
// deployment step — the air-gapped constraint means we cannot rely on a CDN or
// a separate static host.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/ads/api/static", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": `http://127.0.0.1:${process.env.ADS_DEV_API_PORT ?? "8077"}` },
  },
});
