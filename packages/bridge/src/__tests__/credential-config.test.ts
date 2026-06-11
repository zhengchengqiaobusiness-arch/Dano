import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadServerCredentialConfig } from "../credential-config.js";

describe("loadServerCredentialConfig", () => {
  const dirs: string[] = [];

  afterEach(() => {
    for (const dir of dirs.splice(0)) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it("loads local .env values without returning secret material", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-env-"));
    dirs.push(dir);
    fs.writeFileSync(
      path.join(dir, ".env"),
      "OPENAI_API_KEY=sk-local-secret\nDANO_PORT=8080\n",
      "utf8",
    );
    const env: NodeJS.ProcessEnv = {};

    const config = loadServerCredentialConfig({ cwd: dir, env });

    expect(env.OPENAI_API_KEY).toBe("sk-local-secret");
    expect(config.credentialKeys).toEqual(["OPENAI_API_KEY"]);
    expect(JSON.stringify(config)).not.toContain("sk-local-secret");
  });

  it("loads Docker secret files through *_FILE variables", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-secret-"));
    dirs.push(dir);
    const secretFile = path.join(dir, "openai");
    fs.writeFileSync(secretFile, "sk-file-secret\n", "utf8");
    const env: NodeJS.ProcessEnv = {
      OPENAI_API_KEY_FILE: secretFile,
    };

    const config = loadServerCredentialConfig({ cwd: dir, env });

    expect(env.OPENAI_API_KEY).toBe("sk-file-secret");
    expect(config.loadedSecretFiles).toEqual([secretFile]);
    expect(JSON.stringify(config)).not.toContain("sk-file-secret");
  });
});
