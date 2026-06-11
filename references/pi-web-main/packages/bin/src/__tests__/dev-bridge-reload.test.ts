import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createBridgeDevReloadController,
  resolveBridgeDevWatchPath,
} from "../dev-bridge-reload.js";

const tempDirs: string[] = [];

function createWorkspace() {
  const rootDir = mkdtempSync(join(tmpdir(), "pi-web-dev-reload-"));
  tempDirs.push(rootDir);

  const binSrcDir = join(rootDir, "packages", "bin", "src");
  const bridgeSrcDir = join(rootDir, "packages", "bridge", "src");
  const distBinDir = join(rootDir, "dist", "bin");

  mkdirSync(binSrcDir, { recursive: true });
  mkdirSync(bridgeSrcDir, { recursive: true });
  mkdirSync(distBinDir, { recursive: true });

  const entryFile = join(binSrcDir, "index.ts");
  const bridgeFile = join(bridgeSrcDir, "server.ts");
  const distEntryFile = join(distBinDir, "index.js");

  writeFileSync(entryFile, "export default function register() {}\n", "utf8");
  writeFileSync(bridgeFile, "export const bridge = 'v1';\n", "utf8");
  writeFileSync(
    distEntryFile,
    "export default function register() {}\n",
    "utf8",
  );

  return { bridgeFile, distEntryFile, entryFile, rootDir };
}

function waitFor(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

afterEach(() => {
  while (tempDirs.length > 0) {
    const rootDir = tempDirs.pop();
    if (rootDir) {
      rmSync(rootDir, { recursive: true, force: true });
    }
  }
});

describe("bridge dev reload", () => {
  it("resolves the bridge package path from the source entry file", () => {
    const { entryFile, rootDir } = createWorkspace();

    expect(resolveBridgeDevWatchPath(entryFile)).toBe(
      join(rootDir, "packages", "bridge"),
    );
  });

  it("does not enable dev reload for built bin entries", () => {
    const { distEntryFile } = createWorkspace();

    expect(resolveBridgeDevWatchPath(distEntryFile)).toBeUndefined();
    expect(
      createBridgeDevReloadController({
        extensionEntryFile: distEntryFile,
        stop: vi.fn(),
      }),
    ).toBeUndefined();
  });

  it("requests a single stop when bridge sources change", async () => {
    const { bridgeFile, entryFile } = createWorkspace();
    const logger = { error: vi.fn(), log: vi.fn() };

    let resolveStop!: () => void;
    const stopPromise = new Promise<void>(resolve => {
      resolveStop = resolve;
    });
    const stop = vi.fn(async () => {
      resolveStop();
    });

    const controller = createBridgeDevReloadController({
      extensionEntryFile: entryFile,
      stop,
      debounceMs: 25,
      logger,
    });

    expect(controller).toBeDefined();

    await waitFor(50);
    writeFileSync(bridgeFile, "export const bridge = 'v2';\n", "utf8");

    await Promise.race([
      stopPromise,
      new Promise<never>((_, reject) => {
        setTimeout(() => reject(new Error("watcher timeout")), 3000);
      }),
    ]);

    expect(stop).toHaveBeenCalledTimes(1);
    expect(controller?.reloadRequested()).toBe(true);

    controller?.dispose();
  });
});
