#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { chmodSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const command = process.argv[2];
if (command !== "up" && command !== "down") {
  console.error("Usage: node scripts/local-container.mjs <up|down>");
  process.exit(1);
}

const sourceRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const localRoot = join(tmpdir(), "dano-local-container");
const runtimeDir = join(localRoot, "runtime");
const secretsDir = join(localRoot, "secrets");

mkdirSync(runtimeDir, { recursive: true });
mkdirSync(secretsDir, { recursive: true });
chmodSync(runtimeDir, 0o777);

const result = spawnSync(
  process.execPath,
  [join(sourceRoot, "scripts/deploy-compose.mjs"), command],
  {
    cwd: sourceRoot,
    env: {
      ...process.env,
      DANO_COMPOSE: "podman",
      DANO_RUNTIME_DIR: runtimeDir,
      DANO_SECRETS_DIR: secretsDir,
      DANO_NGINX_PORT: "18082",
      DANO_EXPOSURE_MODE: "http",
    },
    stdio: "inherit",
  },
);

if (result.error) throw result.error;
process.exit(result.status ?? 1);
