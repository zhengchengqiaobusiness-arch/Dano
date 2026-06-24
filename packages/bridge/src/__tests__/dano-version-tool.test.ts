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
  it("reads all package versions from Dano env", () => {
    expect(
      readDanoVersionInfo({
        DANO_PACKAGE_VERSIONS: JSON.stringify([
          { key: "root", packageName: "@dano/dano", version: "0.1.0" },
          { key: "app", packageName: "@dano/app", version: "0.3.4" },
          { key: "bridge", packageName: "@dano/bridge", version: "0.3.4" },
          { key: "svelte", packageName: "@dano/svelte", version: "0.3.4" },
        ]),
      }),
    ).toEqual({
      packages: [
        { key: "root", packageName: "@dano/dano", version: "0.1.0" },
        { key: "app", packageName: "@dano/app", version: "0.3.4" },
        { key: "bridge", packageName: "@dano/bridge", version: "0.3.4" },
        { key: "svelte", packageName: "@dano/svelte", version: "0.3.4" },
      ],
    });
  });

  it("trims env values before exposing them to the agent", () => {
    expect(
      readDanoVersionInfo({
        DANO_PACKAGE_VERSIONS:
          '[{"key":" app ","packageName":" @dano/app ","version":" 0.3.4 "}]',
      }),
    ).toEqual({
      packages: [{ key: "app", packageName: "@dano/app", version: "0.3.4" }],
    });
  });

  it("keeps single-package env compatibility", () => {
    expect(
      readDanoVersionInfo({
        DANO_PACKAGE_NAME: "@dano/app",
        DANO_VERSION: "0.3.4",
      }),
    ).toEqual({
      packages: [{ key: "app", packageName: "@dano/app", version: "0.3.4" }],
    });
  });

  it("falls back loudly when no versions are configured", () => {
    expect(readDanoVersionInfo({})).toEqual({
      packages: [{ key: "app", packageName: "@dano/app", version: "unknown" }],
    });
  });

  it("includes optional build metadata when configured", () => {
    expect(
      readDanoVersionInfo({
        DANO_PACKAGE_VERSIONS:
          '[{"key":"app","packageName":"@dano/app","version":"0.3.4"}]',
        DANO_BUILD_SHA: "abc123",
        DANO_BUILD_TIME: "2026-06-24T00:00:00Z",
      }),
    ).toEqual({
      packages: [{ key: "app", packageName: "@dano/app", version: "0.3.4" }],
      buildSha: "abc123",
      buildTime: "2026-06-24T00:00:00Z",
    });
  });

  it("returns JSON content the assistant can quote directly", async () => {
    const originalPackageVersions = process.env.DANO_PACKAGE_VERSIONS;
    process.env.DANO_PACKAGE_VERSIONS =
      '[{"key":"app","packageName":"@dano/app","version":"0.3.4"}]';

    try {
      await expect(executeVersionTool()).resolves.toMatchObject({
        content: [
          {
            type: "text",
            text: '{"packages":[{"key":"app","packageName":"@dano/app","version":"0.3.4"}]}',
          },
        ],
        details: {
          packages: [
            { key: "app", packageName: "@dano/app", version: "0.3.4" },
          ],
        },
      });
    } finally {
      if (originalPackageVersions === undefined) {
        delete process.env.DANO_PACKAGE_VERSIONS;
      } else {
        process.env.DANO_PACKAGE_VERSIONS = originalPackageVersions;
      }
    }
  });
});
