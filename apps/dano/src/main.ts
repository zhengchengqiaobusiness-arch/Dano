import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import { randomUUID } from "node:crypto";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { createDanoBackend } from "./backend.js";
import type { DanoBackend } from "./backend.js";
import { loadDanoConfig } from "./bridge/dano-config.js";
import { DetachedSessionRegistry } from "./bridge/session-registry.js";
import type { BridgeConfig, UploadConfig } from "./bridge/types.js";
import { createDanoDevReloadController } from "./dev-reload.js";
import { loadDanoRuntime, type DanoRuntime } from "./runtime.js";
import type { BridgeEmptyStateConfig } from "../types/protocol.js";

const DEFAULT_DANO_PORT = 8080;
const DEFAULT_DANO_HOST = "0.0.0.0";
const DEFAULT_DANO_RUNTIME_DIR = "/opt/dano/runtime-data";
const DEFAULT_DANO_SESSIONS_DIR = ".dano/sessions";
const DEFAULT_DANO_UPLOAD_DIR = ".dano/uploads";
const DEFAULT_DANO_UPLOAD_MAX_TOTAL_BYTES = 10 * 1024 * 1024 * 1024;
const DEFAULT_DANO_UPLOAD_DRAFT_TTL_MS = 2 * 60 * 60 * 1000;
const DEFAULT_DANO_UPLOAD_REFERENCED_TTL_MS = 24 * 60 * 60 * 1000;
const DEFAULT_DANO_UPLOAD_ORPHANED_TTL_MS = 5 * 60 * 1000;
const DEFAULT_DANO_UPLOAD_CLEANUP_INTERVAL_MS = 60 * 60 * 1000;
const DEFAULT_PRODUCT_NAME = "Dano";
const DEFAULT_RUNTIME_SETTINGS_FILES = [
  "SYSTEM.md",
  "settings.json",
  "heimdall.json",
] as const;
const DANO_HEIMDALL_SANDBOX_ENV_ALLOW = [
  "PATH",
  "HOME",
  "SHELL",
  "USER",
  "LOGNAME",
  "LANG",
  "LC_*",
  "TMPDIR",
  "DANO_URL",
  "DANO_TENANT_KEY",
] as const;
const DANO_HEIMDALL_OA_ENV = ["DANO_URL", "DANO_TENANT_KEY"] as const;
const DEFAULT_EMPTY_STATE: BridgeEmptyStateConfig = {
  mode: "text",
  content: "给 {产品名称} 发消息",
};

interface DanoPackageInfo {
  name: string;
  version: string;
}

export interface DanoServerOptions {
  cwd: string;
  host: string;
  port: number;
  defaultWorkspacePath: string;
  agentConfigDir: string;
  sessionsRootPath: string;
  productName: string;
  emptyState: BridgeEmptyStateConfig;
  upload: UploadConfig;
  staticDir?: string;
  help: boolean;
}

function printHelp(): void {
  console.log(`Dano server

Usage:
  node dist/server/main.js [--host <host>] [--port <number>] [--default-workspace <path>]

Options:
  --host <host>              Host to bind (default: ${DEFAULT_DANO_HOST})
  --port <number>            Port to bind (default: ${DEFAULT_DANO_PORT})
  --default-workspace <path> Deprecated; new sessions use DANO_RUNTIME_DIR/workspaces/ws_<random>
  --sessions-root <path>     Directory for session jsonl files (env: DANO_SESSIONS_ROOT, default: <default-workspace>/${DEFAULT_DANO_SESSIONS_DIR})
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

function parsePositiveInteger(
  value: string | undefined,
  fallback: number,
): number {
  const parsed = parseInteger(value, fallback);
  return parsed > 0 ? parsed : fallback;
}

function readHost(env: Record<string, string | undefined>): string {
  return env.DANO_HOST?.trim() || env.HOST?.trim() || DEFAULT_DANO_HOST;
}

function readPort(env: Record<string, string | undefined>): number {
  return parseInteger(
    env.DANO_PORT?.trim() || env.PORT?.trim(),
    DEFAULT_DANO_PORT,
  );
}

function readRuntimeRootPath(env: Record<string, string | undefined>): string {
  return env.DANO_RUNTIME_DIR?.trim() || DEFAULT_DANO_RUNTIME_DIR;
}

function readDefaultWorkspacePath(runtimeRootPath: string): string {
  return join(runtimeRootPath, "workspaces", `ws_${randomUUID()}`);
}

function readAgentConfigDir(
  env: Record<string, string | undefined>,
  runtimeRootPath: string,
): string {
  return (
    env.PI_CODING_AGENT_DIR?.trim() ||
    join(runtimeRootPath, "default-settings", ".pi", "agent")
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

function readUploadConfig(
  env: Record<string, string | undefined>,
  runtimeRootPath: string,
): UploadConfig {
  return {
    uploadDir:
      env.DANO_UPLOAD_DIR?.trim() || join(runtimeRootPath, DEFAULT_DANO_UPLOAD_DIR),
    maxTotalBytes: parsePositiveInteger(
      env.DANO_UPLOAD_MAX_TOTAL_BYTES?.trim(),
      DEFAULT_DANO_UPLOAD_MAX_TOTAL_BYTES,
    ),
    draftTtlMs: parsePositiveInteger(
      env.DANO_UPLOAD_DRAFT_TTL_MS?.trim(),
      DEFAULT_DANO_UPLOAD_DRAFT_TTL_MS,
    ),
    referencedTtlMs: parsePositiveInteger(
      env.DANO_UPLOAD_REFERENCED_TTL_MS?.trim(),
      DEFAULT_DANO_UPLOAD_REFERENCED_TTL_MS,
    ),
    orphanedTtlMs: parsePositiveInteger(
      env.DANO_UPLOAD_ORPHANED_TTL_MS?.trim(),
      DEFAULT_DANO_UPLOAD_ORPHANED_TTL_MS,
    ),
    cleanupIntervalMs: parsePositiveInteger(
      env.DANO_UPLOAD_CLEANUP_INTERVAL_MS?.trim(),
      DEFAULT_DANO_UPLOAD_CLEANUP_INTERVAL_MS,
    ),
  };
}

function findNearestProductPackageJson(startDir: string): string | undefined {
  let current = resolve(startDir);

  for (;;) {
    const candidate = join(current, "package.json");
    const packageInfo = readPackageInfo(candidate);
    if (packageInfo && packageInfo.name !== "@dano/app") {
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

function resolveStaticDirCandidate(entryFile: string): string | undefined {
  const resolvedEntryFile = resolve(entryFile);
  const sourceMarker = `${sep}apps${sep}dano${sep}src${sep}`;
  const sourceIndex = resolvedEntryFile.lastIndexOf(sourceMarker);
  if (sourceIndex !== -1) {
    return join(
      resolvedEntryFile.slice(0, sourceIndex),
      "apps",
      "dano",
      "dist",
      "web",
    );
  }

  const serverMarker = `${sep}dist${sep}server${sep}`;
  const serverIndex = resolvedEntryFile.lastIndexOf(serverMarker);
  if (serverIndex !== -1) {
    return join(resolvedEntryFile.slice(0, serverIndex), "dist", "web");
  }

  return undefined;
}

export function resolveDefaultStaticDir(entryFile: string): string | undefined {
  const candidate = resolveStaticDirCandidate(entryFile);
  if (!candidate || !existsSync(join(candidate, "index.html"))) {
    return undefined;
  }

  return resolve(candidate);
}

function readPackageInfo(path: string): DanoPackageInfo | undefined {
  if (!existsSync(path)) return undefined;

  try {
    const raw = JSON.parse(readFileSync(path, "utf8")) as {
      name?: unknown;
      version?: unknown;
    };
    return typeof raw.name === "string" && typeof raw.version === "string"
      ? { name: raw.name, version: raw.version }
      : undefined;
  } catch {
    return undefined;
  }
}

export function readDanoPackageInfo(cwd: string): DanoPackageInfo {
  const packagedRoot = readPackageInfo(
    join(cwd, "package-versions", "package.json"),
  );
  if (packagedRoot) return packagedRoot;

  const devRoot = findNearestProductPackageJson(cwd);
  if (devRoot) {
    return (
      readPackageInfo(devRoot) ?? { name: "@dano/dano", version: "unknown" }
    );
  }

  return { name: "@dano/dano", version: "unknown" };
}

export function parseDanoServerOptions(
  argv: string[],
  env: Record<string, string | undefined> = process.env,
): DanoServerOptions {
  let host = readHost(env);
  let port = readPort(env);
  const runtimeRootPath = readRuntimeRootPath(env);
  let sessionsRootPath = readSessionsRootPath(env);
  let productName = readProductName(env);
  let emptyState = readEmptyStateConfig(env);
  const upload = readUploadConfig(env, runtimeRootPath);
  const staticDirOverride = env.DANO_STATIC_DIR?.trim();
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
        port = parseInteger(next, DEFAULT_DANO_PORT);
        index++;
        continue;
      }
      case "--default-workspace": {
        const next = argv[index + 1];
        if (!next || next.startsWith("--")) {
          throw new Error("Missing value for --default-workspace");
        }
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
  const resolvedRuntimeRootPath = resolve(cwd, runtimeRootPath);
  const resolvedDefaultWorkspacePath = readDefaultWorkspacePath(
    resolvedRuntimeRootPath,
  );
  return {
    cwd,
    host,
    port,
    defaultWorkspacePath: resolvedDefaultWorkspacePath,
    agentConfigDir: resolve(
      cwd,
      readAgentConfigDir(env, resolvedRuntimeRootPath),
    ),
    sessionsRootPath: resolve(
      cwd,
      sessionsRootPath ??
        join(resolvedDefaultWorkspacePath, DEFAULT_DANO_SESSIONS_DIR),
    ),
    productName,
    emptyState,
    upload: {
      ...upload,
      uploadDir: resolve(cwd, upload.uploadDir),
    },
    staticDir: staticDirOverride
      ? resolve(cwd, staticDirOverride)
      : resolveDefaultStaticDir(fileURLToPath(import.meta.url)),
    help,
  };
}

function ensureDefaultWorkspace(path: string): string {
  mkdirSync(path, { recursive: true });
  return path;
}

export function initializeDanoAgentSettings(
  agentDir: string,
  sourceCwd: string,
): void {
  const runtimeDefaultsDir = findNearestRuntimeDefaultsDir(sourceCwd);
  if (!runtimeDefaultsDir) {
    return;
  }

  const targetSettingsDir = agentDir;
  mkdirSync(targetSettingsDir, { recursive: true });

  for (const fileName of DEFAULT_RUNTIME_SETTINGS_FILES) {
    const sourcePath = join(runtimeDefaultsDir, fileName);
    const targetPath = join(targetSettingsDir, fileName);
    if (existsSync(sourcePath) && !existsSync(targetPath)) {
      copyFileSync(sourcePath, targetPath);
    }
  }

  migrateHeimdallRuntimeSettings(join(targetSettingsDir, "heimdall.json"));
}

function mergeStringArray(value: unknown, required: readonly string[]): string[] {
  const existing = Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
  return [...new Set([...existing, ...required])];
}

function globPatternMatches(pattern: string, value: string): boolean {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
  return new RegExp(`^${escaped}$`).test(value);
}

function canRemoveDanoEnvDenyPattern(pattern: string, allow: readonly string[]): boolean {
  if (!DANO_HEIMDALL_OA_ENV.some(value => globPatternMatches(pattern, value))) {
    return false;
  }

  return !allow.some(item => {
    if ((DANO_HEIMDALL_SANDBOX_ENV_ALLOW as readonly string[]).includes(item)) {
      return false;
    }
    return item.includes("*") || globPatternMatches(pattern, item);
  });
}

function migrateHeimdallRuntimeSettings(path: string): void {
  if (!existsSync(path)) return;

  let settings: unknown;
  try {
    settings = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return;
  }
  if (!settings || typeof settings !== "object" || Array.isArray(settings)) return;

  const root = settings as { sandbox?: unknown };
  if (!root.sandbox || typeof root.sandbox !== "object" || Array.isArray(root.sandbox)) {
    root.sandbox = {};
  }

  const sandbox = root.sandbox as { userNamespace?: unknown; env?: unknown };
  if (sandbox.userNamespace === undefined) {
    sandbox.userNamespace = false;
  }

  if (!sandbox.env || typeof sandbox.env !== "object" || Array.isArray(sandbox.env)) {
    sandbox.env = {};
  }
  const env = sandbox.env as { allow?: unknown; deny?: unknown };
  const allow = mergeStringArray(env.allow, DANO_HEIMDALL_SANDBOX_ENV_ALLOW);
  env.allow = allow;
  env.deny = Array.isArray(env.deny)
    ? env.deny.filter(
        (item): item is string =>
          typeof item === "string" && !canRemoveDanoEnvDenyPattern(item, allow),
      )
    : [];
  writeFileSync(path, `${JSON.stringify(root, null, 2)}\n`);
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

async function runDanoServer(
  runtime: DanoRuntime,
  config: BridgeConfig,
  options: DanoServerOptions,
  entryFile: string,
  backend: DanoBackend,
  sessionRegistry: DetachedSessionRegistry,
): Promise<boolean> {
  let resolveStopped: (() => void) | undefined;
  const stopped = new Promise<void>(resolve => {
    resolveStopped = resolve;
  });

  const bridgeController = await runtime.startDanoServer(config, {
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

  console.log(`[dano] Server URL: ${bridgeUrl}`);
  console.log(`[dano] HTTP API: ${bridgeUrl}/api/clients`);
  console.log(`[dano] SSE events: ${bridgeUrl}/api/clients/<clientId>/events`);
  if (options.staticDir) {
    console.log(`[dano] Static Dir: ${options.staticDir}`);
  }
  console.log(`[dano] Default Workspace: ${config.defaultWorkspacePath}`);
  console.log(`[dano] Sessions Root: ${options.sessionsRootPath}`);

  const requestStop = async (): Promise<void> => {
    await bridgeController.stop().catch(error => {
      console.error("[dano] Failed to stop Dano server:", error);
    });
  };

  const devReload = createDanoDevReloadController({
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

async function runDanoMain(): Promise<number> {
  let options: DanoServerOptions;
  try {
    options = parseDanoServerOptions(process.argv.slice(2));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`[dano] ${message}`);
    printHelp();
    return 1;
  }

  if (options.help) {
    printHelp();
    return 0;
  }

  const thisFile = fileURLToPath(import.meta.url);
  const packageInfo = readDanoPackageInfo(options.cwd);
  process.env.DANO_PACKAGE_NAME ??= packageInfo.name;
  process.env.DANO_VERSION ??= packageInfo.version;
  const danoConfig = loadDanoConfig({ cwd: options.cwd });
  const defaultWorkspacePath = ensureDefaultWorkspace(options.defaultWorkspacePath);
  if (!process.env.PI_CODING_AGENT_DIR?.trim()) {
    process.env.PI_CODING_AGENT_DIR = options.agentConfigDir;
  }
  initializeDanoAgentSettings(options.agentConfigDir, options.cwd);
  mkdirSync(options.sessionsRootPath, { recursive: true });
  process.env.DANO_SESSIONS_ROOT = options.sessionsRootPath;
  process.env.PI_WEB_SESSIONS_ROOT = options.sessionsRootPath;
  const defaultSessionDir = workspaceSessionDirPath(
    options.sessionsRootPath,
    defaultWorkspacePath,
  );
  const backend = await createDanoBackend({
    cwd: defaultWorkspacePath,
    sessionDir: defaultSessionDir,
    danoConfig,
  });
  const sessionRegistry = new DetachedSessionRegistry(
    backend.context.state.cwd,
  );

  try {
    while (true) {
      const runtime = await loadDanoRuntime(thisFile);
      const config: BridgeConfig = {
        ...runtime.DEFAULT_BRIDGE_CONFIG,
        host: options.host,
        port: options.port,
        defaultWorkspacePath,
        productName: options.productName,
        emptyState: options.emptyState,
        upload: options.upload,
        quickActions: danoConfig.quickActions ?? [],
        staticDir: options.staticDir,
      };

      const reloadRequested = await runDanoServer(
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

      console.log("[dano] Dano server runtime reloaded.");
    }
  } finally {
    sessionRegistry.dispose();
    await backend.dispose();
  }
}

const invokedPath = process.argv[1];
const thisFile = fileURLToPath(import.meta.url);
if (invokedPath && realpathSync(resolve(invokedPath)) === realpathSync(resolve(thisFile))) {
  runDanoMain().then(
    code => {
      process.exitCode = code;
    },
    error => {
      console.error(error instanceof Error ? error.message : String(error));
      process.exitCode = 1;
    },
  );
}
