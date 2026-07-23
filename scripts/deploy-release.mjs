#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import {
  chmodSync,
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolveDeploymentExposure } from "./deploy-exposure.mjs";
import { readEnvValues, updateEnvFile } from "./deploy-env-file.mjs";

const composeBin = process.env.DANO_COMPOSE || "docker";
const composeArgs = composeBin === "podman" ? ["compose"] : ["compose"];
const buildParent = process.env.DANO_BUILD_PARENT_DIR || tmpdir();
const deployDir = process.env.DANO_DEPLOY_DIR || "/opt/dano/deploy";
const runtimeDir = process.env.DANO_RUNTIME_DIR || "/opt/dano/runtime-data";
const secretsDir = process.env.DANO_SECRETS_DIR || join(deployDir, ".secrets");
const nginxConf =
  process.env.DANO_NGINX_CONF || join(deployDir, "nginx/default.conf.template");
const nginxDemoAuthConf = join(deployDir, "nginx/demo-auth.conf.template");
const nginxSharedDir = join(deployDir, "nginx/shared");
const exposureComposeFile = join(deployDir, "docker-compose.exposure.yml");
const envPath = join(deployDir, ".env");
const exposure = resolveDeploymentExposure(process.env, { baseDir: deployDir });
const runtimeOwner = "1000:1000";
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

function cloneRepo(repoUrl, targetDir) {
  const filteredArgs = ["clone", "--filter=blob:none", repoUrl, targetDir];
  console.log(`[deploy-release] git ${filteredArgs.join(" ")}`);
  const filtered = spawnSync("git", filteredArgs, {
    stdio: "inherit",
    env: process.env,
  });
  if (filtered.error) throw filtered.error;
  if (filtered.status === 0) return;

  console.log("[deploy-release] partial clone failed; retrying full clone");
  rmSync(targetDir, { recursive: true, force: true });
  run("git", ["clone", repoUrl, targetDir]);
}

function requireValue(name, value) {
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

function prepareRuntimeDir() {
  mkdirSync(runtimeDir, { recursive: true });
  chmodSync(runtimeDir, 0o755);
  run("chown", ["-R", runtimeOwner, runtimeDir]);
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
  cloneRepo(repoUrl, buildDir);
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
  prepareRuntimeDir();
  mkdirSync(secretsDir, { recursive: true, mode: 0o700 });
  cpSync(join(buildDir, "docker-compose.yml"), join(deployDir, "docker-compose.yml"));
  cpSync(
    join(buildDir, `deploy/compose/${exposure.mode}.yml`),
    exposureComposeFile,
  );
  cpSync(
    join(buildDir, `deploy/nginx/${exposure.mode}.conf.template`),
    nginxConf,
  );
  cpSync(
    join(buildDir, "deploy/nginx/demo-auth.conf.template"),
    nginxDemoAuthConf,
  );
  cpSync(join(buildDir, "deploy/nginx/shared"), nginxSharedDir, {
    recursive: true,
  });
  updateEnvFile(envPath, {
    DANO_IMAGE: image,
    DANO_RUNTIME_DIR: runtimeDir,
    DANO_SECRETS_DIR: secretsDir,
    DANO_NGINX_CONF: nginxConf,
    DANO_NGINX_DEMO_AUTH_CONF: nginxDemoAuthConf,
    DANO_NGINX_SHARED_DIR: nginxSharedDir,
    DANO_EXPOSURE_MODE: exposure.mode,
    ...exposure.tlsEnv,
  });
  run(process.execPath, [
    new URL("./init-demo-auth.mjs", import.meta.url).pathname,
    "--file",
    envPath,
  ]);
  process.loadEnvFile(envPath);
  const persistedEnv = readEnvValues(readFileSync(envPath, "utf8"));
  for (const name of [
    "DANO_AUTH_JWT_SECRET",
    "DANO_DEMO_JWT",
    "DANO_DEMO_COOKIE_EXPIRES",
  ]) {
    const value = persistedEnv.get(name);
    if (!value) throw new Error(`${name} was not persisted by Demo authentication`);
    process.env[name] = value;
  }

  run(
    composeBin,
    [
      ...composeArgs,
      "-f",
      "docker-compose.yml",
      "-f",
      "docker-compose.exposure.yml",
      "--env-file",
      ".env",
      "up",
      "-d",
      "--no-build",
    ],
    { cwd: deployDir },
  );
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
