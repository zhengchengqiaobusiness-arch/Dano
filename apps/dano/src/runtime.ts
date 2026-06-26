import { dirname, join, resolve } from "node:path";
import {
  DEFAULT_BRIDGE_CONFIG as STATIC_DEFAULT_BRIDGE_CONFIG,
  type BridgeConfig,
} from "./bridge/types.js";
import { resolveDanoDevWatchPath } from "./dev-reload.js";
import {
  startDanoServer as staticStartDanoServer,
  type DanoServerController,
  type StartDanoServerOptions,
} from "./server.js";

export interface DanoRuntime {
  DEFAULT_BRIDGE_CONFIG: BridgeConfig;
  startDanoServer: (
    config: BridgeConfig,
    options?: StartDanoServerOptions,
  ) => Promise<DanoServerController>;
}

const staticRuntime: DanoRuntime = {
  DEFAULT_BRIDGE_CONFIG: STATIC_DEFAULT_BRIDGE_CONFIG,
  startDanoServer: staticStartDanoServer,
};

export async function loadDanoRuntime(
  entryFile: string,
): Promise<DanoRuntime> {
  if (!resolveDanoDevWatchPath(entryFile)) {
    return staticRuntime;
  }

  const runtimeEntryPath = join(
    dirname(resolve(entryFile)),
    "runtime-entry.ts",
  );

  const jitiModuleId = ["jiti", "static"].join("/");
  const { createJiti } = await import(jitiModuleId);
  const jiti = createJiti(import.meta.url, {
    moduleCache: false,
  });

  return jiti.import(runtimeEntryPath, { default: true });
}
