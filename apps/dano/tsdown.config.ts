import { fileURLToPath } from "node:url";
import { defineConfig } from "tsdown";

export default defineConfig({
  platform: "node",
  target: "node22",
  format: "esm",
  fixedExtension: false,
  clean: true,
  sourcemap: false,
  entry: ["src/main.ts", "src/protected-main.ts", "src/bridge/heimdall-worker-tools.ts", "src/bridge/linux-process-privacy.ts", "src/bridge/worker-broker-entry.ts", "src/bridge/start-worker-broker.ts", "src/bridge/worker-workspace.ts", "src/bridge/protected-host-entry.ts", "src/bridge/protected-supervisor.ts", "src/bridge/memory-tokenizer.ts", "src/bridge/memory-credential-store.ts", "src/bridge/memory-recovery-journal.ts"],
  outDir: "dist/server",
  copy: [{ from: "src/bridge/python/*.py", to: "dist/server/python" }],
  dts: false,
  tsconfig: "./tsconfig.server.json",
  alias: {
    "@dano/types": fileURLToPath(new URL("./types", import.meta.url)),
  },
});
