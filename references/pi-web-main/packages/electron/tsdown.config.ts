import { defineConfig } from "tsdown";

export default defineConfig({
  platform: "node",
  target: "node20",
  format: "esm",
  fixedExtension: false,
  clean: true,
  sourcemap: false,
  entry: {
    main: "src/main.ts",
  },
  outDir: "./dist",
  dts: false,
  tsconfig: "./tsconfig.json",
  deps: {
    alwaysBundle: [/^@pi-web\/bridge(?:\/.*)?$/],
    neverBundle: [/^@earendil-works\//, /^electron$/, /^node:/, "ws"],
  },
});
