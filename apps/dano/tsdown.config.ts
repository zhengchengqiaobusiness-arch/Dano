import { fileURLToPath } from "node:url";
import { defineConfig } from "tsdown";

export default defineConfig({
  platform: "node",
  target: "node22",
  format: "esm",
  fixedExtension: false,
  clean: true,
  sourcemap: false,
  entry: ["src/main.ts"],
  outDir: "dist/server",
  dts: false,
  tsconfig: "./tsconfig.server.json",
  alias: {
    "@dano/types": fileURLToPath(new URL("./types", import.meta.url)),
  },
});
