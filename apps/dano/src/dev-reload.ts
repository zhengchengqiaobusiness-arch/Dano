import {
  existsSync,
  readdirSync,
  statSync,
  watch,
  type FSWatcher,
} from "node:fs";
import { dirname, join, resolve, sep } from "node:path";

const DEV_APP_ENTRY_SEGMENT = `${sep}apps${sep}dano${sep}src${sep}`;
const DEFAULT_DEBOUNCE_MS = 75;
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
  logger?: Pick<Console, "error" | "log">;
}

interface DanoDevReloadChange {
  eventType: "change" | "rename";
  filename?: string;
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

function listDanoWatchDirectories(rootPath: string): string[] {
  const directories: string[] = [];

  const visit = (directoryPath: string): void => {
    directories.push(directoryPath);

    for (const entry of readdirSync(directoryPath, { withFileTypes: true })) {
      if (!entry.isDirectory() || IGNORED_DIRECTORIES.has(entry.name)) {
        continue;
      }
      visit(join(directoryPath, entry.name));
    }
  };

  visit(rootPath);
  return directories;
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
  const logger = options.logger ?? console;
  const appRoot = dirname(watchPath);
  const watchPaths = [
    watchPath,
    join(appRoot, "types"),
  ].filter(path => existsSync(path) && statSync(path).isDirectory());

  let disposed = false;
  let requested = false;
  let debounceTimer: NodeJS.Timeout | undefined;
  let rescanScheduled = false;
  let lastChange: DanoDevReloadChange = { eventType: "change" };
  const watchers = new Map<string, FSWatcher>();

  const cleanupWatchers = (): void => {
    for (const watcher of watchers.values()) {
      watcher.close();
    }
    watchers.clear();
  };

  const refreshWatchers = (): void => {
    if (disposed) {
      return;
    }

    const nextDirectories = new Set(
      watchPaths.flatMap(path => listDanoWatchDirectories(path)),
    );

    for (const [directoryPath, watcher] of watchers) {
      if (nextDirectories.has(directoryPath)) {
        continue;
      }
      watcher.close();
      watchers.delete(directoryPath);
    }

    for (const directoryPath of nextDirectories) {
      if (watchers.has(directoryPath)) {
        continue;
      }

      const watcher = watch(directoryPath, (eventType, filename) => {
        if (disposed || requested) {
          return;
        }

        lastChange = {
          eventType,
          filename:
            typeof filename === "string" && filename.length > 0
              ? filename
              : undefined,
        };

        if (eventType === "rename" && !rescanScheduled) {
          rescanScheduled = true;
          queueMicrotask(() => {
            rescanScheduled = false;
            if (!disposed) {
              refreshWatchers();
            }
          });
        }

        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          if (disposed || requested) {
            return;
          }

          requested = true;
          const changedPath = lastChange.filename
            ? ` (${lastChange.filename})`
            : "";
          logger.log(
            `[dano] Detected source change${changedPath}; reloading Dano runtime...`,
          );

          Promise.resolve(options.stop()).catch(error => {
            logger.error(
              "[dano] Failed to stop Dano server for hot reload:",
              error,
            );
          });
        }, debounceMs);
      });

      watcher.on("error", error => {
        if (!disposed) {
          logger.error("[dano] Dano server dev watcher error:", error);
        }
      });

      watchers.set(directoryPath, watcher);
    }
  };

  refreshWatchers();
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
      cleanupWatchers();
    },
  };
}
