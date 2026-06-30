import {
  existsSync,
  readdirSync,
  statSync,
} from "node:fs";
import { dirname, join, resolve, sep } from "node:path";

const DEV_APP_ENTRY_SEGMENT = `${sep}apps${sep}dano${sep}src${sep}`;
const DEFAULT_DEBOUNCE_MS = 75;
const DEFAULT_POLL_INTERVAL_MS = 500;
const IGNORED_DIRECTORIES = new Set([".git", "dist", "node_modules"]);

export interface DanoDevReloadController {
  readonly watchPath: string;
  readonly watchPaths: readonly string[];
  reloadRequested(): boolean;
  dispose(): void;
}

export interface DanoDevReloadControllerOptions {
  entryFile: string;
  stop: () => Promise<void> | void;
  debounceMs?: number;
  pollIntervalMs?: number;
  logger?: Pick<Console, "error" | "log">;
}

export function resolveDanoDevWatchPath(
  entryFile: string,
): string | undefined {
  const resolvedEntryFile = resolve(entryFile);
  const markerIndex = resolvedEntryFile.lastIndexOf(DEV_APP_ENTRY_SEGMENT);
  if (markerIndex === -1) {
    return undefined;
  }

  const workspaceRoot = resolvedEntryFile.slice(0, markerIndex);
  return join(workspaceRoot, "apps", "dano", "src");
}

function snapshotDanoWatchFiles(rootPaths: readonly string[]): Map<string, string> {
  const snapshot = new Map<string, string>();

  const visit = (directoryPath: string): void => {
    let entries;
    try {
      entries = readdirSync(directoryPath, { withFileTypes: true });
    } catch {
      return;
    }

    for (const entry of entries) {
      const entryPath = join(directoryPath, entry.name);
      if (entry.isDirectory()) {
        if (!IGNORED_DIRECTORIES.has(entry.name)) {
          visit(entryPath);
        }
        continue;
      }

      if (!entry.isFile()) {
        continue;
      }

      try {
        const stats = statSync(entryPath);
        snapshot.set(entryPath, `${stats.mtimeMs}:${stats.size}`);
      } catch {
        continue;
      }
    }
  };

  for (const rootPath of rootPaths) {
    visit(rootPath);
  }

  return snapshot;
}

function findDanoWatchChange(
  previous: ReadonlyMap<string, string>,
  next: ReadonlyMap<string, string>,
): string | undefined {
  for (const [path, signature] of next) {
    if (previous.get(path) !== signature) {
      return path;
    }
  }

  for (const path of previous.keys()) {
    if (!next.has(path)) {
      return path;
    }
  }

  return undefined;
}

export function createDanoDevReloadController(
  options: DanoDevReloadControllerOptions,
): DanoDevReloadController | undefined {
  const watchPath = resolveDanoDevWatchPath(options.entryFile);
  if (
    !watchPath ||
    !existsSync(watchPath) ||
    !statSync(watchPath).isDirectory()
  ) {
    return undefined;
  }

  const debounceMs = options.debounceMs ?? DEFAULT_DEBOUNCE_MS;
  const pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const logger = options.logger ?? console;
  const appRoot = dirname(watchPath);
  const watchPaths = [
    watchPath,
    join(appRoot, "types"),
  ].filter(path => existsSync(path) && statSync(path).isDirectory());

  let disposed = false;
  let requested = false;
  let debounceTimer: NodeJS.Timeout | undefined;
  let snapshot = snapshotDanoWatchFiles(watchPaths);
  const pollTimer = setInterval(() => {
    if (disposed || requested) {
      return;
    }

    const nextSnapshot = snapshotDanoWatchFiles(watchPaths);
    const changedPath = findDanoWatchChange(snapshot, nextSnapshot);
    snapshot = nextSnapshot;

    if (!changedPath) {
      return;
    }

    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      if (disposed || requested) {
        return;
      }

      requested = true;
      logger.log(
        `[dano] Detected source change (${changedPath}); reloading Dano runtime...`,
      );

      Promise.resolve(options.stop()).catch(error => {
        logger.error(
          "[dano] Failed to stop Dano server for hot reload:",
          error,
        );
      });
    }, debounceMs);
  }, pollIntervalMs);

  logger.log(`[dano] Watching Dano sources: ${watchPaths.join(", ")}`);

  return {
    watchPath,
    watchPaths,
    reloadRequested() {
      return requested;
    },
    dispose() {
      if (disposed) {
        return;
      }
      disposed = true;
      clearTimeout(debounceTimer);
      clearInterval(pollTimer);
    },
  };
}
