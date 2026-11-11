import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: resolve(__dirname, "client"),
  base: "/app/",
  plugins: [react()],
  resolve: { alias: { "@shared": resolve(__dirname, "shared") } },
  build: { outDir: resolve(__dirname, "dist/app"), emptyOutDir: true, sourcemap: false },
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
  preview: { port: 4173 },
});
