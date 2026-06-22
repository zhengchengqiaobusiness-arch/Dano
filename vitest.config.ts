import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: [
      "apps/dano/src/**/*.test.ts",
      "packages/bridge/src/**/*.test.ts",
      "packages/svelte/src/**/*.test.ts",
    ],
  },
});
