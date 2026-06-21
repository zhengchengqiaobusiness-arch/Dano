import { defineConfig } from "tsdown";

export default defineConfig({
  platform: "node",
  target: "node20",
  format: "esm",
  fixedExtension: false,
  clean: true,
  sourcemap: false,
  entry: {
    index: "src/index.ts",
  },
  outDir: "../../dist/bin",
  dts: false,
  tsconfig: "./tsconfig.json",
  deps: {
    alwaysBundle: [/^@pi-web\/bridge(?:\/.*)?$/],
    neverBundle: [/^@mariozechner\//, /^@earendil-works\//, /^node:/, "ws"],
  },
});
