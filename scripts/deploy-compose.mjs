#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { resolveDeploymentExposure } from "./deploy-exposure.mjs";

const command = process.argv[2] ?? "up";
const composeBin = process.env.DANO_COMPOSE || "docker";
const baseArgs = composeBin === "podman" ? ["compose"] : ["compose"];
const hasEnvFile = existsSync(".env");
if (hasEnvFile) process.loadEnvFile(".env");
const envFileArgs = hasEnvFile ? ["--env-file", ".env"] : [];
const exposure = resolveDeploymentExposure(process.env, {
  validateTls: command === "up",
});
const exposureComposeFile = existsSync("docker-compose.exposure.yml")
  ? "docker-compose.exposure.yml"
  : `deploy/compose/${exposure.mode}.yml`;
const usesReleaseAssets = exposureComposeFile === "docker-compose.exposure.yml";
const sourceRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const composeEnv = {
  ...process.env,
  ...exposure.tlsEnv,
  ...(!usesReleaseAssets && !process.env.DANO_NGINX_CONF
    ? {
        DANO_NGINX_CONF: join(
          sourceRoot,
          `deploy/nginx/${exposure.mode}.conf.template`,
        ),
      }
    : {}),
  ...(!usesReleaseAssets && !process.env.DANO_NGINX_SHARED_DIR
    ? { DANO_NGINX_SHARED_DIR: join(sourceRoot, "deploy/nginx/shared") }
    : {}),
};
const composeFileArgs = [
  "-f",
  "docker-compose.yml",
  "-f",
  exposureComposeFile,
];

function run(args) {
  const result = spawnSync(
    composeBin,
    [...baseArgs, ...composeFileArgs, ...envFileArgs, ...args],
    {
      stdio: "inherit",
      env: composeEnv,
    },
  );
  if (result.error) {
    console.error(`[deploy-compose] ${result.error.message}`);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

switch (command) {
  case "up": {
    if (process.env.DANO_IMAGE?.trim()) {
      run(["pull", "app"]);
    }
    run(["up", "-d", "--no-build"]);
    break;
  }
  case "down":
    run(["down"]);
    break;
  case "stop":
    run(["stop"]);
    break;
  case "logs":
    run(["logs", "-f", "--tail", "100"]);
    break;
  case "ps":
    run(["ps"]);
    break;
  default:
    console.error("Usage: node scripts/deploy-compose.mjs <up|stop|down|logs|ps>");
    process.exit(1);
}
