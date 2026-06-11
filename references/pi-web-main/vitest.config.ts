import { defineConfig } from "vitest/config";
import FailureOnlyReporter from "./scripts/vitest-failure-only-reporter.js";

export default defineConfig({
  test: {
    include: [
      "packages/bridge/**/*.test.ts",
      "packages/svelte/src/**/*.test.ts",
    ],
    reporters: [new FailureOnlyReporter()],
  },
});
