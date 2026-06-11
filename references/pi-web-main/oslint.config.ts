import { defineConfig } from "oxlint";

export default defineConfig({
  plugins: ["typescript", "vitest", "import", "jsdoc", "node", "promise"],
  categories: {
    correctness: "error",
    perf: "error",
    suspicious: "error",
  },
});
