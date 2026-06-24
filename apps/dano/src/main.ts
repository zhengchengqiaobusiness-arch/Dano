import { copyFileSync, existsSync, mkdirSync, realpathSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadDanoConfig } from "@dano/bridge/dano-config";
import { DetachedSessionRegistry } from "@dano/bridge/session-registry";
import type { BridgeConfig, BridgeEmptyStateConfig } from "@dano/bridge/types";
import { createStandaloneBridgeContext } from "./backend.js";
import type { StandaloneBridgeBackend } from "./backend.js";
import { createStandaloneDevReloadController } from "./dev-reload.js";
import { loadStandaloneRuntime, type StandaloneRuntime } from "./runtime.js";

const DEFAULT_STANDALONE_PORT = 8080;
const DEFAULT_STANDALONE_HOST = "0.0.0.0";
const DEFAULT_STANDALONE_WORKSPACE = "/tmp/dano";
const DEFAULT_STANDALONE_SESSIONS_DIR = ".dano/sessions";
const DEFAULT_PRODUCT_NAME = "Dano";
const DEFAULT_RUNTIME_SETTINGS_FILES = [
  "SYSTEM.md",
  "settings.json",
  "heimdall.json",
] as const;
const DEFAULT_EMPTY_STATE: BridgeEmptyStateConfig = {
  mode: "text",
  content: "给 {产品名称} 发消息",
};

export interface StandaloneMainOptions {
  cwd: string;
  host: string;
  port: number;
  defaultWorkspacePath: string;
  sessionsRootPath: string;
  productName: string;
  emptyState: BridgeEmptyStateConfig;
  staticDir?: string;
  help: boolean;
}

function printHelp(): void {
  console.log(`pi-web standalone bridge

Usage:
  node dist/bridge/standalone/main.js [--host <host>] [--port <number>] [--default-workspace <path>]

Options:
  --host <host>              Host to bind (default: ${DEFAULT_STANDALONE_HOST})
  --port <number>            Port to bind (default: ${DEFAULT_STANDALONE_PORT})
  --default-workspace <path> Default workspace path (env: DANO_DEFAULT_WORKSPACE_PATH, default: ${DEFAULT_STANDALONE_WORKSPACE})
  --sessions-root <path>     Directory for session jsonl files (env: DANO_SESSIONS_ROOT, default: <default-workspace>/${DEFAULT_STANDALONE_SESSIONS_DIR})
  --product-name <name>      Product name shown in browser UI (env: DANO_PRODUCT_NAME, default: ${DEFAULT_PRODUCT_NAME})
  --empty-state-text <text>  Empty transcript text (env: DANO_EMPTY_STATE_TEXT, default: ${DEFAULT_EMPTY_STATE.content})
  --empty-state-html <html>  Empty transcript HTML (env: DANO_EMPTY_STATE_HTML)
  --help                     Show this help
`);
}

function parseInteger(value: string | undefined, fallback: number): number {
  if (!value) {
    return fallback;
  }

  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function readHost(env: Record<string, string | undefined>): string {
  return env.DANO_HOST?.trim() || env.HOST?.trim() || DEFAULT_STANDALONE_HOST;
}

function readPort(env: Record<string, string | undefined>): number {
  return parseInteger(
    env.DANO_PORT?.trim() || env.PORT?.trim(),
    DEFAULT_STANDALONE_PORT,
  );
}

function readDefaultWorkspacePath(
  env: Record<string, string | undefined>,
): string {
  return (
    env.DANO_DEFAULT_WORKSPACE_PATH?.trim() ||
    env.DANO_DEFAULT_WORKSPACE?.trim() ||
    DEFAULT_STANDALONE_WORKSPACE
  );
}

function readSessionsRootPath(
  env: Record<string, string | undefined>,
): string | undefined {
  return (
    env.DANO_SESSIONS_ROOT?.trim() ||
    env.PI_WEB_SESSIONS_ROOT?.trim() ||
    undefined
  );
}

function readProductName(env: Record<string, string | undefined>): string {
  return env.DANO_PRODUCT_NAME?.trim() || DEFAULT_PRODUCT_NAME;
}

function readEmptyStateConfig(
  env: Record<string, string | undefined>,
): BridgeEmptyStateConfig {
  const html = env.DANO_EMPTY_STATE_HTML;
  if (html?.trim()) {
    return { mode: "html", content: html };
  }

  const text = env.DANO_EMPTY_STATE_TEXT;
  if (text?.trim()) {
    return { mode: "text", content: text };
  }

  return DEFAULT_EMPTY_STATE;
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

function findNearestRuntimeDefaultsDir(startDir: string): string | undefined {
  let current = resolve(startDir);

  for (;;) {
    const candidate = join(current, "deploy", "runtime-defaults");
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
  env: Record<string, string | undefined> = process.env,
): StandaloneMainOptions {
  let host = readHost(env);
  let port = readPort(env);
  let defaultWorkspacePath = readDefaultWorkspacePath(env);
  let sessionsRootPath = readSessionsRootPath(env);
  let productName = readProductName(env);
  let emptyState = readEmptyStateConfig(env);
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
      case "--host": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --host");
        }
        host = next;
        index++;
        continue;
      }
      case "--port": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --port");
        }
        port = parseInteger(next, DEFAULT_STANDALONE_PORT);
        index++;
        continue;
      }
      case "--default-workspace": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --default-workspace");
        }
        defaultWorkspacePath = next;
        index++;
        continue;
      }
      case "--sessions-root": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --sessions-root");
        }
        sessionsRootPath = next;
        index++;
        continue;
      }
      case "--product-name": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --product-name");
        }
        productName = next.trim() || DEFAULT_PRODUCT_NAME;
        index++;
        continue;
      }
      case "--empty-state-text": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --empty-state-text");
        }
        emptyState = { mode: "text", content: next };
        index++;
        continue;
      }
      case "--empty-state-html": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --empty-state-html");
        }
        emptyState = { mode: "html", content: next };
        index++;
        continue;
      }
      default:
        throw new Error(`Unknown option: ${token}`);
    }
  }

  const cwd = process.cwd();
  const resolvedDefaultWorkspacePath = resolve(cwd, defaultWorkspacePath);
  return {
    cwd,
    host,
    port,
    defaultWorkspacePath: resolvedDefaultWorkspacePath,
    sessionsRootPath: resolve(
      cwd,
      sessionsRootPath ??
        join(resolvedDefaultWorkspacePath, DEFAULT_STANDALONE_SESSIONS_DIR),
    ),
    productName,
    emptyState,
    staticDir: resolveDefaultStaticDir(cwd),
    help,
  };
}

function ensureDefaultWorkspace(path: string): string {
  mkdirSync(path, { recursive: true });
  return path;
}

export function initializeStandaloneWorkspaceSettings(
  workspacePath: string,
  sourceCwd: string,
): void {
  const runtimeDefaultsDir = findNearestRuntimeDefaultsDir(sourceCwd);
  if (!runtimeDefaultsDir) {
    return;
  }

  const targetSettingsDir = join(workspacePath, ".pi");
  mkdirSync(targetSettingsDir, { recursive: true });

  for (const fileName of DEFAULT_RUNTIME_SETTINGS_FILES) {
    const sourcePath = join(runtimeDefaultsDir, fileName);
    const targetPath = join(targetSettingsDir, fileName);
    if (existsSync(sourcePath) && !existsSync(targetPath)) {
      copyFileSync(sourcePath, targetPath);
    }
  }
}

function workspaceSessionDirName(workspacePath: string): string {
  return `--${workspacePath.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`;
}

function workspaceSessionDirPath(
  sessionsRootPath: string,
  workspacePath: string,
): string {
  return join(sessionsRootPath, workspaceSessionDirName(workspacePath));
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

  console.log(`[pi-web] Bridge URL: ${bridgeUrl}`);
  console.log(`[pi-web] HTTP API: ${bridgeUrl}/api/clients`);
  console.log(`[pi-web] SSE events: ${bridgeUrl}/api/clients/<clientId>/events`);
  if (options.staticDir) {
    console.log(`[pi-web] Static Dir: ${options.staticDir}`);
  }
  console.log(`[pi-web] Default Workspace: ${config.defaultWorkspacePath}`);
  console.log(`[pi-web] Sessions Root: ${options.sessionsRootPath}`);

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
  const danoConfig = loadDanoConfig({ cwd: options.cwd });
  const defaultWorkspacePath = ensureDefaultWorkspace(options.defaultWorkspacePath);
  initializeStandaloneWorkspaceSettings(defaultWorkspacePath, options.cwd);
  mkdirSync(options.sessionsRootPath, { recursive: true });
  process.env.DANO_SESSIONS_ROOT = options.sessionsRootPath;
  process.env.PI_WEB_SESSIONS_ROOT = options.sessionsRootPath;
  const defaultSessionDir = workspaceSessionDirPath(
    options.sessionsRootPath,
    defaultWorkspacePath,
  );
  const backend = await createStandaloneBridgeContext({
    cwd: defaultWorkspacePath,
    sessionDir: defaultSessionDir,
    danoConfig,
  });
  const sessionRegistry = new DetachedSessionRegistry(
    backend.context.state.cwd,
  );

  try {
    while (true) {
      const runtime = await loadStandaloneRuntime(thisFile);
      const config: BridgeConfig = {
        ...runtime.DEFAULT_BRIDGE_CONFIG,
        host: options.host,
        port: options.port,
        defaultWorkspacePath,
        productName: options.productName,
        emptyState: options.emptyState,
        quickActions: danoConfig.quickActions ?? [],
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
