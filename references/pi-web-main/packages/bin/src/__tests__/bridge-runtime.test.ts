import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadBridgeRuntime } from "../bridge-runtime.js";

const tempDirs: string[] = [];

function createWorkspace() {
  const rootDir = mkdtempSync(join(tmpdir(), "pi-web-bridge-runtime-"));
  tempDirs.push(rootDir);

  const binSrcDir = join(rootDir, "packages", "bin", "src");
  const bridgeDir = join(rootDir, "packages", "bridge");
  mkdirSync(binSrcDir, { recursive: true });
  mkdirSync(bridgeDir, { recursive: true });

  const entryFile = join(binSrcDir, "index.ts");
  const runtimeEntryFile = join(binSrcDir, "runtime-bridge-entry.ts");

  writeFileSync(entryFile, "export default function register() {}\n", "utf8");

  return { entryFile, runtimeEntryFile };
}

function writeRuntimeEntry(runtimeEntryFile: string, version: string): void {
  writeFileSync(
    runtimeEntryFile,
    `export default {
  DEFAULT_BRIDGE_CONFIG: { host: "127.0.0.1", port: ${version === "v1" ? 7001 : 7002} },
  async startBridge() {
    return ${JSON.stringify(version)};
  },
};\n`,
    "utf8",
  );
}

afterEach(() => {
  while (tempDirs.length > 0) {
    const rootDir = tempDirs.pop();
    if (rootDir) {
      rmSync(rootDir, { recursive: true, force: true });
    }
  }
});

describe("bridge runtime loader", () => {
  it("reloads the dev runtime without keeping stale module cache", async () => {
    const { entryFile, runtimeEntryFile } = createWorkspace();

    writeRuntimeEntry(runtimeEntryFile, "v1");
    const firstRuntime = await loadBridgeRuntime(entryFile);
    expect(firstRuntime.DEFAULT_BRIDGE_CONFIG.port).toBe(7001);
    await expect(
      firstRuntime.startBridge({} as never, {} as never, () => {}),
    ).resolves.toBe("v1");

    writeRuntimeEntry(runtimeEntryFile, "v2");
    const secondRuntime = await loadBridgeRuntime(entryFile);
    expect(secondRuntime.DEFAULT_BRIDGE_CONFIG.port).toBe(7002);
    await expect(
      secondRuntime.startBridge({} as never, {} as never, () => {}),
    ).resolves.toBe("v2");
  });
});
