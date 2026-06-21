import { execFileSync } from "node:child_process";
import {
  chmodSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

const deployScript = new URL(
  "../../../../scripts/deploy-compose.mjs",
  import.meta.url,
).pathname;
const tempDirs: string[] = [];

function run(command: string, options: { envFile?: boolean; image?: string } = {}) {
  const cwd = mkdtempSync(join(tmpdir(), "dano-deploy-compose-"));
  tempDirs.push(cwd);
  const logPath = join(cwd, "compose.log");
  const composePath = join(cwd, "compose");
  writeFileSync(
    composePath,
    `#!/usr/bin/env node
import { appendFileSync } from "node:fs";
appendFileSync(process.env.DANO_COMPOSE_LOG, JSON.stringify(process.argv.slice(2)) + "\\n");
`,
  );
  chmodSync(composePath, 0o755);
  if (options.envFile) writeFileSync(join(cwd, ".env"), "DANO_TEST=1\n");

  const env: NodeJS.ProcessEnv = {
    ...process.env,
    DANO_COMPOSE: composePath,
    DANO_COMPOSE_LOG: logPath,
  };
  delete env.DANO_IMAGE;
  if (options.image) env.DANO_IMAGE = options.image;
  execFileSync(process.execPath, [deployScript, command], { cwd, env });

  return readFileSync(logPath, "utf8")
    .trim()
    .split("\n")
    .map(line => JSON.parse(line));
}

afterEach(() => {
  for (const path of tempDirs.splice(0)) rmSync(path, { recursive: true });
});

describe("deploy compose wrapper", () => {
  it("builds and starts when no image is provided", () => {
    expect(run("up")).toEqual([["compose", "up", "--build", "-d"]]);
  });

  it("pulls and starts a prebuilt image without building", () => {
    expect(run("up", { image: "example/dano:latest" })).toEqual([
      ["compose", "pull", "app"],
      ["compose", "up", "-d", "--no-build"],
    ]);
  });

  it("stops without removing containers", () => {
    expect(run("stop")).toEqual([["compose", "stop"]]);
  });

  it("keeps down as the explicit removal command", () => {
    expect(run("down")).toEqual([["compose", "down"]]);
  });

  it("passes the local env file to compose", () => {
    expect(run("ps", { envFile: true })).toEqual([
      ["compose", "--env-file", ".env", "ps"],
    ]);
  });
});
