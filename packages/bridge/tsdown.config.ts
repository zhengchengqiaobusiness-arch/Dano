import { defineConfig } from "tsdown";

export default defineConfig({
  platform: "node",
  target: "node20",
  format: "esm",
  fixedExtension: false,
  clean: true,
  sourcemap: false,
  entry: ["src/**/*.ts", "!src/**/__tests__/**"],
  root: "src",
  outDir: "../../dist/bridge",
  unbundle: true,
  dts: false,
  tsconfig: "./tsconfig.json",
  deps: {
    skipNodeModulesBundle: true,
  },
});
