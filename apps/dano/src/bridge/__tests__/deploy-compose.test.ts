import { execFileSync } from "node:child_process";
import {
  chmodSync,
  existsSync,
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
  "../../../../../scripts/deploy-compose.mjs",
  import.meta.url,
).pathname;
const releaseScript = new URL(
  "../../../../../scripts/deploy-release.mjs",
  import.meta.url,
).pathname;
const composeFile = new URL(
  "../../../../../docker-compose.yml",
  import.meta.url,
).pathname;
const dockerfile = new URL("../../../../../Dockerfile", import.meta.url).pathname;
const dockerignoreFile = new URL(
  "../../../../../.dockerignore",
  import.meta.url,
).pathname;
const entrypointFile = new URL(
  "../../../../../deploy/docker-entrypoint.sh",
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

function runRelease(options: { env?: NodeJS.ProcessEnv } = {}) {
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
if (process.env.DANO_FAKE_GIT_REJECT_FILTER && args.includes("--filter=blob:none")) process.exit(129);
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
  writeFileSync(
    join(fakeBin, "chown"),
    `#!/usr/bin/env node
import { appendFileSync } from "node:fs";
appendFileSync(process.env.DANO_COMMAND_LOG, JSON.stringify(["chown", ...process.argv.slice(2)]) + "\\n");
`,
  );
  chmodSync(join(fakeBin, "git"), 0o755);
  chmodSync(join(fakeBin, "compose"), 0o755);
  chmodSync(join(fakeBin, "chown"), 0o755);

  execFileSync(process.execPath, [releaseScript], {
    cwd,
    env: {
      ...process.env,
      ...options.env,
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

  return {
    buildParent,
    deployDir,
    runtimeDir,
    nginxConf,
    logLines: readFileSync(logPath, "utf8").trim().split("\n"),
  };
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
    expect(compose).toContain("name: dano");
    expect(compose).not.toContain("build:");
    expect(compose).not.toContain("./runtime-data");
    expect(compose).not.toContain("cap_add:");
    expect(compose).not.toContain("seccomp=unconfined");
    expect(compose).toContain(
      "DANO_RUNTIME_DIR: /opt/dano/runtime-data",
    );
    expect(compose).toContain(
      "DANO_UPLOAD_DIR: /opt/dano/runtime-data/.dano/uploads",
    );
    expect(compose).toContain(
      "${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}:/opt/dano/runtime-data",
    );
    expect(compose).not.toContain(":/tmp/dano");
    expect(compose).toContain(
      "${DANO_NGINX_CONF:-/opt/dano/deploy/nginx/default.conf}",
    );
  });

  it("rewrites Debian apt sources to Aliyun HTTP mirrors", () => {
    const dockerfileText = readFileSync(dockerfile, "utf8");

    expect(dockerfileText).toContain("https\\?://deb.debian.org/debian");
    expect(dockerfileText).toContain(
      "http://mirrors.aliyun.com/debian-security",
    );
    expect(dockerfileText).toContain("http://mirrors.aliyun.com/debian");
    expect(dockerfileText).not.toContain(
      "https://mirrors.aliyun.com/debian-security",
    );
    expect(dockerfileText).not.toContain("https://mirrors.aliyun.com/debian");
  });

  it("keeps local package stores out of the Docker build context", () => {
    const dockerignoreText = readFileSync(dockerignoreFile, "utf8");

    expect(dockerignoreText).toContain("node_modules");
    expect(dockerignoreText).toContain(".pnpm-store");
  });

  it("runs the production container as the non-root node user", () => {
    const dockerfileText = readFileSync(dockerfile, "utf8");

    expect(dockerfileText).toContain("ENV HOME=/home/node");
    expect(dockerfileText).toContain("ENV DANO_RUNTIME_DIR=/opt/dano/runtime-data");
    expect(dockerfileText).toContain("ENV HEIMDALL_BWRAP_BIND_KERNEL_FS=1");
    expect(dockerfileText).toContain("ENV HEIMDALL_BWRAP_BIND_ROOT=/opt/dano");
    expect(dockerfileText).not.toContain("COPY patches");
    expect(dockerfileText).not.toContain("patched_heimdall_dir=");
    expect(dockerfileText).not.toContain("prod_heimdall_dir=");
    expect(dockerfileText).toContain("mkdir -p /opt/dano/runtime-data");
    expect(dockerfileText).toContain("chown -R node:node /opt/dano /home/node");
    expect(dockerfileText).toContain("chmod 0755 /usr/bin/bwrap");
    expect(dockerfileText).not.toContain("chmod u+s /usr/bin/bwrap");
    expect(dockerfileText).not.toContain("/tmp/dano");
    expect(dockerfileText).toContain("USER node");
    expect(dockerfileText.indexOf("USER node")).toBeGreaterThan(
      dockerfileText.indexOf("apt-get install"),
    );
  });

  it("initializes runtime defaults in the global agent config directory", () => {
    const cwd = mkdtempSync(join(tmpdir(), "dano-entrypoint-test-"));
    tempDirs.push(cwd);
    const defaultsDir = join(cwd, "defaults");
    const runtimeDir = join(cwd, "runtime-data");
    const workspaceDir = join(cwd, "workspace");
    const agentDir = join(runtimeDir, "default-settings/.pi/agent");
    const agentDirOut = join(cwd, "agent-dir.txt");

    mkdirSync(defaultsDir, { recursive: true });
    mkdirSync(agentDir, { recursive: true });
    writeFileSync(join(defaultsDir, "SYSTEM.md"), "default system\n");
    writeFileSync(join(defaultsDir, "settings.json"), "{\"default\":true}\n");
    writeFileSync(join(defaultsDir, "heimdall.json"), "{\"guard\":true}\n");
    writeFileSync(join(agentDir, "settings.json"), "{\"custom\":true}\n");

    execFileSync("sh", [
      entrypointFile,
      "/bin/sh",
      "-c",
      'printf "%s" "$PI_CODING_AGENT_DIR" > "$DANO_AGENT_DIR_OUT"',
    ], {
      env: {
        ...process.env,
        PATH: "/usr/bin:/bin",
        DANO_RUNTIME_DEFAULTS_DIR: defaultsDir,
        DANO_RUNTIME_DIR: runtimeDir,
        DANO_DEFAULT_WORKSPACE_PATH: workspaceDir,
        DANO_AGENT_DIR_OUT: agentDirOut,
      },
    });

    expect(readFileSync(agentDirOut, "utf8")).toBe(agentDir);
    expect(readFileSync(join(agentDir, "SYSTEM.md"), "utf8")).toBe(
      "default system\n",
    );
    expect(readFileSync(join(agentDir, "settings.json"), "utf8")).toBe(
      "{\"custom\":true}\n",
    );
    expect(readFileSync(join(agentDir, "heimdall.json"), "utf8")).toBe(
      "{\"guard\":true}\n",
    );
    expect(existsSync(join(workspaceDir, ".pi"))).toBe(false);
  });

  it("honors an existing PI_CODING_AGENT_DIR", () => {
    const cwd = mkdtempSync(join(tmpdir(), "dano-entrypoint-test-"));
    tempDirs.push(cwd);
    const defaultsDir = join(cwd, "defaults");
    const runtimeDir = join(cwd, "runtime-data");
    const agentDir = join(cwd, "custom-agent");
    const agentDirOut = join(cwd, "agent-dir.txt");

    mkdirSync(defaultsDir, { recursive: true });
    writeFileSync(join(defaultsDir, "SYSTEM.md"), "default system\n");
    writeFileSync(join(defaultsDir, "settings.json"), "{\"default\":true}\n");
    writeFileSync(join(defaultsDir, "heimdall.json"), "{\"guard\":true}\n");

    execFileSync("sh", [
      entrypointFile,
      "/bin/sh",
      "-c",
      'printf "%s" "$PI_CODING_AGENT_DIR" > "$DANO_AGENT_DIR_OUT"',
    ], {
      env: {
        ...process.env,
        PATH: "/usr/bin:/bin",
        DANO_RUNTIME_DEFAULTS_DIR: defaultsDir,
        DANO_RUNTIME_DIR: runtimeDir,
        PI_CODING_AGENT_DIR: agentDir,
        DANO_AGENT_DIR_OUT: agentDirOut,
      },
    });

    expect(readFileSync(agentDirOut, "utf8")).toBe(agentDir);
    expect(readFileSync(join(agentDir, "SYSTEM.md"), "utf8")).toBe(
      "default system\n",
    );
    expect(existsSync(join(runtimeDir, "default-settings"))).toBe(false);
  });

  it("builds releases from a temporary checkout and cleans it up", () => {
    const { buildParent, deployDir, runtimeDir, nginxConf, logLines } =
      runRelease();

    expect(readdirSync(buildParent)).toEqual([]);
    expect(readFileSync(join(deployDir, "docker-compose.yml"), "utf8")).toContain(
      "services:",
    );
    expect(readFileSync(nginxConf, "utf8")).toContain("server");
    expect(readFileSync(join(deployDir, ".env"), "utf8")).toContain(
      `DANO_RUNTIME_DIR=${runtimeDir}`,
    );
    expect(JSON.parse(logLines[0])).toEqual([
      "build",
      "--build-arg",
      "NPM_REGISTRY=https://mirrors.cloud.tencent.com/npm/",
      "-t",
      "dano-app:abc123",
      expect.stringContaining("dano-build-"),
    ]);
    expect(JSON.parse(logLines[1])).toEqual([
      "chown",
      "-R",
      "1000:1000",
      runtimeDir,
    ]);
    expect(JSON.parse(logLines[2])).toEqual([
      "compose",
      "--env-file",
      ".env",
      "up",
      "-d",
      "--no-build",
    ]);
    expect(logLines[3]).toBe("smoke");
  });

  it("lets NPM_REGISTRY override the default release build registry", () => {
    const { logLines } = runRelease({
      env: { NPM_REGISTRY: "https://registry.npmjs.org/" },
    });

    expect(JSON.parse(logLines[0])).toContain(
      "NPM_REGISTRY=https://registry.npmjs.org/",
    );
  });

  it("falls back to full clone when partial clone is unavailable", () => {
    const { logLines } = runRelease({
      env: { DANO_FAKE_GIT_REJECT_FILTER: "1" },
    });

    expect(JSON.parse(logLines[0])[0]).toBe("build");
  });

  it("keeps NPM_CONFIG_REGISTRY as a release build compatibility override", () => {
    const { logLines } = runRelease({
      env: { NPM_CONFIG_REGISTRY: "https://registry.example.test/" },
    });

    expect(JSON.parse(logLines[0])).toContain(
      "NPM_REGISTRY=https://registry.example.test/",
    );
  });
});
