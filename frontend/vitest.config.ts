import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@shared": resolve(__dirname, "shared") } },
  test: { globals: true, environment: "jsdom", include: ["shared/**/*.test.ts", "client/src/**/*.test.ts", "admin/src/**/*.test.ts"] },
});
