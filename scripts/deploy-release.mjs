#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const composeBin = process.env.DANO_COMPOSE || "docker";
const composeArgs = composeBin === "podman" ? ["compose"] : ["compose"];
const buildParent = process.env.DANO_BUILD_PARENT_DIR || tmpdir();
const deployDir = process.env.DANO_DEPLOY_DIR || "/opt/dano/deploy";
const runtimeDir = process.env.DANO_RUNTIME_DIR || "/opt/dano/runtime-data";
const secretsDir = process.env.DANO_SECRETS_DIR || join(deployDir, ".secrets");
const nginxConf = process.env.DANO_NGINX_CONF || join(deployDir, "nginx/default.conf");
const envPath = join(deployDir, ".env");
const defaultNpmRegistry = "https://mirrors.cloud.tencent.com/npm/";
const npmRegistry =
  process.env.NPM_REGISTRY ||
  process.env.NPM_CONFIG_REGISTRY ||
  defaultNpmRegistry;

let buildDir;

function output(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    encoding: "utf8",
    env: process.env,
  });
  if (result.status !== 0) return "";
  return result.stdout.trim();
}

function run(command, args, options = {}) {
  console.log(`[deploy-release] ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    stdio: "inherit",
    env: options.env || process.env,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`${command} exited with ${result.status ?? 1}`);
  }
}

function requireValue(name, value) {
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

function updateEnvFile(values) {
  const current = existsSync(envPath) ? readFileSync(envPath, "utf8") : "";
  const lines = current.split(/\r?\n/).filter(line => line.length > 0);
  const seen = new Set();
  const next = lines.map(line => {
    const match = line.match(/^([A-Z0-9_]+)=/);
    if (!match || !(match[1] in values)) return line;
    seen.add(match[1]);
    return `${match[1]}=${values[match[1]]}`;
  });
  for (const [key, value] of Object.entries(values)) {
    if (!seen.has(key)) next.push(`${key}=${value}`);
  }
  writeFileSync(envPath, `${next.join("\n")}\n`, { mode: 0o600 });
}

try {
  const repoUrl = requireValue(
    "DANO_REPO_URL",
    process.env.DANO_REPO_URL ||
      output("git", ["config", "--get", "remote.upstream.url"]) ||
      output("git", ["config", "--get", "remote.origin.url"]),
  );
  const gitRef =
    process.env.DANO_GIT_REF ||
    output("git", ["rev-parse", "HEAD"]) ||
    "main";
  const shortRef = gitRef.replace(/[^a-zA-Z0-9_.-]/g, "-").slice(0, 12);
  const image = process.env.DANO_IMAGE || `dano-app:${shortRef}`;

  buildDir = mkdtempSync(join(buildParent, "dano-build-"));
  run("git", ["clone", "--filter=blob:none", repoUrl, buildDir]);
  run("git", ["checkout", gitRef], { cwd: buildDir });
  run(composeBin, [
    "build",
    "--build-arg",
    `NPM_REGISTRY=${npmRegistry}`,
    "-t",
    image,
    buildDir,
  ]);

  mkdirSync(join(deployDir, "nginx"), { recursive: true });
  mkdirSync(runtimeDir, { recursive: true });
  mkdirSync(secretsDir, { recursive: true, mode: 0o700 });
  cpSync(join(buildDir, "docker-compose.yml"), join(deployDir, "docker-compose.yml"));
  cpSync(join(buildDir, "deploy/nginx/default.conf"), nginxConf);
  updateEnvFile({
    DANO_IMAGE: image,
    DANO_RUNTIME_DIR: runtimeDir,
    DANO_SECRETS_DIR: secretsDir,
    DANO_NGINX_CONF: nginxConf,
  });

  run(composeBin, [...composeArgs, "--env-file", ".env", "up", "-d", "--no-build"], {
    cwd: deployDir,
  });
  run(process.execPath, [join(buildDir, "scripts/smoke-dano-deploy.mjs")], {
    env: {
      ...process.env,
      DANO_IMAGE: image,
      DANO_RUNTIME_DIR: runtimeDir,
      DANO_SECRETS_DIR: secretsDir,
      DANO_NGINX_CONF: nginxConf,
    },
  });
} finally {
  if (buildDir) {
    rmSync(buildDir, { recursive: true, force: true });
    console.log(`[deploy-release] removed ${buildDir}`);
  }
}
