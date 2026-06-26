import { describe, expect, it } from "vitest";
import { danoVersionTool, readDanoVersionInfo } from "../dano-version-tool.js";

function executeVersionTool() {
  return danoVersionTool.execute(
    "version-1",
    {},
    undefined,
    undefined,
    {} as never,
  );
}

describe("dano version tool", () => {
  it("reads the Dano product version from env", () => {
    expect(
      readDanoVersionInfo({
        DANO_PACKAGE_NAME: "@dano/dano",
        DANO_VERSION: "0.1.0",
      }),
    ).toEqual({ packageName: "@dano/dano", version: "0.1.0" });
  });

  it("trims env values before exposing them to the agent", () => {
    expect(
      readDanoVersionInfo({
        DANO_PACKAGE_NAME: " @dano/dano ",
        DANO_VERSION: " 0.1.0 ",
      }),
    ).toEqual({ packageName: "@dano/dano", version: "0.1.0" });
  });

  it("falls back loudly when no version is configured", () => {
    expect(readDanoVersionInfo({})).toEqual({
      packageName: "@dano/dano",
      version: "unknown",
    });
  });

  it("includes optional build metadata when configured", () => {
    expect(
      readDanoVersionInfo({
        DANO_PACKAGE_NAME: "@dano/dano",
        DANO_VERSION: "0.1.0",
        DANO_BUILD_SHA: "abc123",
        DANO_BUILD_TIME: "2026-06-24T00:00:00Z",
      }),
    ).toEqual({
      packageName: "@dano/dano",
      version: "0.1.0",
      buildSha: "abc123",
      buildTime: "2026-06-24T00:00:00Z",
    });
  });

  it("returns JSON content the assistant can quote directly", async () => {
    const originalPackageName = process.env.DANO_PACKAGE_NAME;
    const originalVersion = process.env.DANO_VERSION;
    process.env.DANO_PACKAGE_NAME = "@dano/dano";
    process.env.DANO_VERSION = "0.1.0";

    try {
      await expect(executeVersionTool()).resolves.toMatchObject({
        content: [
          {
            type: "text",
            text: '{"packageName":"@dano/dano","version":"0.1.0"}',
          },
        ],
        details: { packageName: "@dano/dano", version: "0.1.0" },
      });
    } finally {
      if (originalPackageName === undefined) delete process.env.DANO_PACKAGE_NAME;
      else process.env.DANO_PACKAGE_NAME = originalPackageName;
      if (originalVersion === undefined) delete process.env.DANO_VERSION;
      else process.env.DANO_VERSION = originalVersion;
    }
  });
});
