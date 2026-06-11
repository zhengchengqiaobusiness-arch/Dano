import { existsSync, realpathSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadServerCredentialConfig } from "../credential-config.js";
import type { ChatServerConfig } from "../types.js";
import { DEFAULT_STANDALONE_CONFIG } from "./runtime.js";
import { startStandaloneServer } from "./server.js";

interface MainOptions {
  help: boolean;
  host: string;
  port: number;
  cwd: string;
  staticDir?: string;
  sessionDir?: string;
}

function printHelp(): void {
  console.log(`dano standalone web chat

Usage:
  node dist/bridge/standalone/main.js [--host <host>] [--port <port>]

Options:
  --host <host>         Host to bind (default: DANO_HOST or 127.0.0.1)
  --port <port>         Port to bind (default: DANO_PORT or 8080)
  --static-dir <path>   Built web assets directory (default: nearest web-dist)
  --cwd <path>          Runtime working directory (default: process cwd)
  --session-dir <path>  Runtime session directory
  --help                Show this help
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

  return candidates.find(Boolean);
}

export function parseMainOptions(argv: string[]): MainOptions {
  let host = process.env.DANO_HOST?.trim() || DEFAULT_STANDALONE_CONFIG.host;
  let port = parseInteger(process.env.DANO_PORT, DEFAULT_STANDALONE_CONFIG.port);
  let cwd = process.cwd();
  let staticDir: string | undefined;
  let sessionDir = process.env.DANO_SESSION_DIR?.trim() || undefined;
  let help = false;

  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token || token === "--") {
      continue;
    }

    switch (token) {
      case "--help":
      case "-h":
        help = true;
        break;
      case "--host":
        host = readOptionValue(argv, ++index, "--host");
        break;
      case "--port":
        port = parseInteger(readOptionValue(argv, ++index, "--port"), port);
        break;
      case "--cwd":
        cwd = resolve(readOptionValue(argv, ++index, "--cwd"));
        break;
      case "--static-dir":
        staticDir = resolve(readOptionValue(argv, ++index, "--static-dir"));
        break;
      case "--session-dir":
        sessionDir = resolve(readOptionValue(argv, ++index, "--session-dir"));
        break;
      default:
        throw new Error(`Unknown option: ${token}`);
    }
  }

  return {
    help,
    host,
    port,
    cwd,
    staticDir: staticDir ?? resolveDefaultStaticDir(cwd),
    ...(sessionDir ? { sessionDir } : {}),
  };
}

function readOptionValue(argv: string[], index: number, name: string): string {
  const value = argv[index];
  if (!value || value.startsWith("--")) {
    throw new Error(`Missing value for ${name}`);
  }
  return value;
}

async function runMain(): Promise<number> {
  let options: MainOptions;
  try {
    options = parseMainOptions(process.argv.slice(2));
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    printHelp();
    return 1;
  }

  if (options.help) {
    printHelp();
    return 0;
  }

  const credentials = loadServerCredentialConfig({ cwd: options.cwd });
  const config: ChatServerConfig = {
    ...DEFAULT_STANDALONE_CONFIG,
    host: options.host,
    port: options.port,
    cwd: options.cwd,
    staticDir: options.staticDir,
    sessionDir: options.sessionDir,
  };

  const server = await startStandaloneServer(config);
  console.log(`[dano] Web URL: ${server.getUrl()}`);
  if (config.staticDir) {
    console.log(`[dano] Static Dir: ${config.staticDir}`);
  }
  if (credentials.loadedEnvFile) {
    console.log(`[dano] Loaded .env: ${credentials.loadedEnvFile}`);
  }
  if (credentials.credentialKeys.length > 0) {
    console.log(`[dano] Server credential keys: ${credentials.credentialKeys.join(", ")}`);
  }

  return new Promise<number>(resolveExitCode => {
    let stopping = false;
    const stop = async () => {
      if (stopping) {
        return;
      }
      stopping = true;
      await server.stop();
      resolveExitCode(0);
    };

    process.once("SIGINT", () => {
      void stop();
    });
    process.once("SIGTERM", () => {
      void stop();
    });
  });
}

const invokedPath = process.argv[1];
const thisFile = fileURLToPath(import.meta.url);
if (invokedPath && realpathSync(resolve(invokedPath)) === realpathSync(resolve(thisFile))) {
  runMain().then(
    code => {
      process.exitCode = code;
    },
    error => {
      console.error(error instanceof Error ? error.message : String(error));
      process.exitCode = 1;
    },
  );
}
