import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI backend runs on 127.0.0.1:8000. In dev, /api and the
// local-video media streams are proxied there so no CORS setup is needed.
// The production build outputs to ../dist, which app/main.py serves.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
