import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: [
      "packages/bridge/src/**/*.test.ts",
      "packages/svelte/src/**/*.test.ts",
    ],
  },
});
