import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: resolve(__dirname, "admin"),
  base: "/admin/",
  plugins: [react()],
  resolve: { alias: { "@shared": resolve(__dirname, "shared") } },
  build: { outDir: resolve(__dirname, "dist/admin"), emptyOutDir: true, sourcemap: false },
  server: { port: 5174, proxy: { "/api": "http://localhost:8000" } },
  preview: { port: 4174 },
});
