import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";

const typesRoot = fileURLToPath(new URL("./apps/dano/types/", import.meta.url));

export default defineConfig({
  plugins: [svelte()],
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
