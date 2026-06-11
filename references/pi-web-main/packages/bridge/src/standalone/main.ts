import { existsSync, realpathSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { DetachedSessionRegistry } from "../session-registry.js";
import type { BridgeConfig } from "../types.js";
import { createStandaloneBridgeContext } from "./backend.js";
import type { StandaloneBridgeBackend } from "./backend.js";
import { createStandaloneDevReloadController } from "./dev-reload.js";
import { loadStandaloneRuntime, type StandaloneRuntime } from "./runtime.js";

const DEFAULT_STANDALONE_PORT = 8080;

export interface StandaloneMainOptions {
  cwd: string;
  port: number;
  staticDir?: string;
  help: boolean;
}

function printHelp(): void {
  console.log(`pi-web standalone bridge

Usage:
  node dist/bridge/standalone/main.js [--port <number>]

Options:
  --port <number>  Port to bind (default: ${DEFAULT_STANDALONE_PORT})
  --help           Show this help
`);
}

function parseInteger(value: string | undefined, fallback: number): number {
  if (!value) {
    return fallback;
  }

  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function findNearestWebDist(startDir: string): string | undefined {
  let current = resolve(startDir);

  for (;;) {
    const candidate = join(current, "web-dist");
    if (existsSync(candidate)) {
      return candidate;
    }

    const parent = dirname(current);
    if (parent === current) {
      return undefined;
    }
    current = parent;
  }
}

function resolveDefaultStaticDir(cwd: string): string | undefined {
  const candidates = [
    findNearestWebDist(cwd),
    findNearestWebDist(process.cwd()),
    findNearestWebDist(dirname(fileURLToPath(import.meta.url))),
  ];

  for (const candidate of candidates) {
    if (candidate) {
      return resolve(candidate);
    }
  }

  return undefined;
}

export function parseStandaloneMainOptions(
  argv: string[],
): StandaloneMainOptions {
  let port = DEFAULT_STANDALONE_PORT;
  let help = false;

  for (let index = 0; index < argv.length; index++) {
    const token = argv[index];
    if (!token || token === "--") {
      continue;
    }

    switch (token) {
      case "--help":
      case "-h":
        help = true;
        continue;
      case "--port": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --port");
        }
        port = parseInteger(next, DEFAULT_STANDALONE_PORT);
        index++;
        continue;
      }
      default:
        throw new Error(`Unknown option: ${token}`);
    }
  }

  const cwd = process.cwd();
  return {
    cwd,
    port,
    staticDir: resolveDefaultStaticDir(cwd),
    help,
  };
}

async function runStandaloneBridge(
  runtime: StandaloneRuntime,
  config: BridgeConfig,
  options: StandaloneMainOptions,
  entryFile: string,
  backend: StandaloneBridgeBackend,
  sessionRegistry: DetachedSessionRegistry,
): Promise<boolean> {
  let resolveStopped: (() => void) | undefined;
  const stopped = new Promise<void>(resolve => {
    resolveStopped = resolve;
  });

  const bridgeController = await runtime.startStandaloneBridge(config, {
    cwd: options.cwd,
    backend,
    sessionRegistry,
    onShutdown: () => resolveStopped?.(),
  });

  const bridgeUrl = bridgeController.getBridgeUrl();
  if (!bridgeUrl) {
    await bridgeController.stop();
    throw new Error("Bridge started without a reachable URL");
  }

  const wsUrl = `${bridgeUrl.replace(/^http/, "ws")}/ws`;
  console.log(`[pi-web] Bridge URL: ${bridgeUrl}`);
  console.log(`[pi-web] WebSocket: ${wsUrl}`);
  if (options.staticDir) {
    console.log(`[pi-web] Static Dir: ${options.staticDir}`);
  }
  console.log(`[pi-web] Session CWD: ${options.cwd}`);

  const requestStop = async (): Promise<void> => {
    await bridgeController.stop().catch(error => {
      console.error("[pi-web] Failed to stop standalone bridge:", error);
    });
  };

  const devReload = createStandaloneDevReloadController({
    entryFile,
    stop: requestStop,
  });

  const onSigterm = (): void => {
    void requestStop();
  };

  process.on("SIGTERM", onSigterm);

  try {
    await stopped;
  } finally {
    process.off("SIGTERM", onSigterm);
    devReload?.dispose();
  }

  return devReload?.reloadRequested() ?? false;
}

async function runStandaloneMain(): Promise<number> {
  let options: StandaloneMainOptions;
  try {
    options = parseStandaloneMainOptions(process.argv.slice(2));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`[pi-web] ${message}`);
    printHelp();
    return 1;
  }

  if (options.help) {
    printHelp();
    return 0;
  }

  const thisFile = fileURLToPath(import.meta.url);
  const backend = await createStandaloneBridgeContext({ cwd: options.cwd });
  const sessionRegistry = new DetachedSessionRegistry(
    backend.context.state.cwd,
  );

  try {
    while (true) {
      const runtime = await loadStandaloneRuntime(thisFile);
      const config: BridgeConfig = {
        ...runtime.DEFAULT_BRIDGE_CONFIG,
        port: options.port,
        staticDir: options.staticDir,
      };

      const reloadRequested = await runStandaloneBridge(
        runtime,
        config,
        options,
        thisFile,
        backend,
        sessionRegistry,
      );

      if (!reloadRequested) {
        return 0;
      }

      console.log("[pi-web] Standalone bridge runtime reloaded.");
    }
  } finally {
    sessionRegistry.dispose();
    await backend.dispose();
  }
}

const invokedPath = process.argv[1];
const thisFile = fileURLToPath(import.meta.url);
if (invokedPath && realpathSync(resolve(invokedPath)) === realpathSync(resolve(thisFile))) {
  runStandaloneMain().then(
    code => {
      process.exitCode = code;
    },
    error => {
      console.error(error instanceof Error ? error.message : String(error));
      process.exitCode = 1;
    },
  );
}
