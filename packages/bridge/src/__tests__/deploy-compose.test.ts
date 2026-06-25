import { execFileSync } from "node:child_process";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
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
const releaseScript = new URL(
  "../../../../scripts/deploy-release.mjs",
  import.meta.url,
).pathname;
const composeFile = new URL(
  "../../../../docker-compose.yml",
  import.meta.url,
).pathname;
const tempDirs: string[] = [];

function run(
  command: string,
  options: { envFile?: boolean; image?: string } = {},
) {
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
  it("starts without building when no image is provided", () => {
    expect(run("up")).toEqual([["compose", "up", "-d", "--no-build"]]);
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

  it("keeps production compose independent from the source checkout", () => {
    const compose = readFileSync(composeFile, "utf8");
    expect(compose).not.toContain("build:");
    expect(compose).not.toContain("./runtime-data");
    expect(compose).toContain("${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}:/tmp/dano");
    expect(compose).toContain(
      "${DANO_NGINX_CONF:-/opt/dano/deploy/nginx/default.conf}",
    );
  });

  it("builds releases from a temporary checkout and cleans it up", () => {
    const cwd = mkdtempSync(join(tmpdir(), "dano-release-test-"));
    tempDirs.push(cwd);
    const fakeRepo = join(cwd, "repo");
    const fakeBin = join(cwd, "bin");
    const buildParent = join(cwd, "tmp");
    const deployDir = join(cwd, "deploy");
    const runtimeDir = join(cwd, "runtime");
    const secretsDir = join(deployDir, ".secrets");
    const nginxConf = join(deployDir, "nginx/default.conf");
    const logPath = join(cwd, "commands.log");

    mkdirSync(join(fakeRepo, "deploy/nginx"), { recursive: true });
    mkdirSync(join(fakeRepo, "scripts"), { recursive: true });
    mkdirSync(fakeBin);
    mkdirSync(buildParent);
    writeFileSync(join(fakeRepo, "docker-compose.yml"), "services:\n  app:\n");
    writeFileSync(join(fakeRepo, "deploy/nginx/default.conf"), "server {}\n");
    writeFileSync(
      join(fakeRepo, "scripts/smoke-dano-deploy.mjs"),
      "import { appendFileSync } from 'node:fs'; appendFileSync(process.env.DANO_COMMAND_LOG, 'smoke\\n');\n",
    );
    writeFileSync(
      join(fakeBin, "git"),
      `#!/usr/bin/env node
import { cpSync } from "node:fs";
const args = process.argv.slice(2);
if (args[0] === "clone") cpSync(process.env.DANO_FAKE_REPO, args.at(-1), { recursive: true });
`,
    );
    writeFileSync(
      join(fakeBin, "compose"),
      `#!/usr/bin/env node
import { appendFileSync } from "node:fs";
appendFileSync(process.env.DANO_COMMAND_LOG, JSON.stringify(process.argv.slice(2)) + "\\n");
`,
    );
    chmodSync(join(fakeBin, "git"), 0o755);
    chmodSync(join(fakeBin, "compose"), 0o755);

    execFileSync(process.execPath, [releaseScript], {
      cwd,
      env: {
        ...process.env,
        PATH: `${fakeBin}:${process.env.PATH}`,
        DANO_COMMAND_LOG: logPath,
        DANO_COMPOSE: "compose",
        DANO_FAKE_REPO: fakeRepo,
        DANO_REPO_URL: "fake",
        DANO_GIT_REF: "abc123",
        DANO_BUILD_PARENT_DIR: buildParent,
        DANO_DEPLOY_DIR: deployDir,
        DANO_RUNTIME_DIR: runtimeDir,
        DANO_SECRETS_DIR: secretsDir,
        DANO_NGINX_CONF: nginxConf,
        DANO_SMOKE_BASE_URL: "http://127.0.0.1:18082",
      },
    });

    expect(readdirSync(buildParent)).toEqual([]);
    expect(readFileSync(join(deployDir, "docker-compose.yml"), "utf8")).toContain(
      "services:",
    );
    expect(readFileSync(nginxConf, "utf8")).toContain("server");
    expect(readFileSync(join(deployDir, ".env"), "utf8")).toContain(
      `DANO_RUNTIME_DIR=${runtimeDir}`,
    );
    const logLines = readFileSync(logPath, "utf8").trim().split("\n");
    expect(JSON.parse(logLines[0])).toEqual([
      "build",
      "--build-arg",
      "NPM_CONFIG_REGISTRY=https://registry.npmjs.org/",
      "-t",
      "dano-app:abc123",
      expect.stringContaining("dano-build-"),
    ]);
    expect(JSON.parse(logLines[1])).toEqual([
      "compose",
      "--env-file",
      ".env",
      "up",
      "-d",
      "--no-build",
    ]);
    expect(logLines[2]).toBe("smoke");
  });
});
