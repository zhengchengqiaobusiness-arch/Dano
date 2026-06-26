import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const typesRoot = fileURLToPath(new URL("./apps/dano/types/", import.meta.url));

export default defineConfig({
  resolve: {
    alias: [
      {
        find: /^@dano\/types\//,
        replacement: typesRoot,
      },
    ],
  },
  test: {
    include: [
      "apps/dano/src/**/*.test.ts",
      "apps/dano/web/src/**/*.test.ts",
    ],
  },
});
