import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { loadDanoConfig } from "../dano-config.js";

describe("Dano config", () => {
  it("reads default model and thinking from an explicit config file", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-config-"));
    const configPath = path.join(tmpDir, "dano.config.json");
    fs.writeFileSync(
      configPath,
      JSON.stringify({
        defaultProvider: "xiaomi-token-plan-cn",
        defaultModel: "mimo-v2.5",
        defaultThinkingLevel: "medium",
        defaultProjectTrust: "always",
      }),
    );

    expect(
      loadDanoConfig({
        cwd: tmpDir,
        env: { DANO_CONFIG_PATH: configPath },
      }),
    ).toEqual({
      defaultProvider: "xiaomi-token-plan-cn",
      defaultModel: "mimo-v2.5",
      defaultThinkingLevel: "medium",
      defaultProjectTrust: "always",
    });

    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns an empty config when no Dano config file exists", () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-no-config-"));

    expect(
      loadDanoConfig({
        cwd: tmpDir,
        env: { DANO_CONFIG_PATH: path.join(tmpDir, "missing.json") },
        startDir: tmpDir,
      }),
    ).toEqual({});

    fs.rmSync(tmpDir, { recursive: true, force: true });
  });
});
