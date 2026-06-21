#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

const command = process.argv[2] ?? "up";
const composeBin = process.env.DANO_COMPOSE || "docker";
const baseArgs = composeBin === "podman" ? ["compose"] : ["compose"];
const envFileArgs = existsSync(".env") ? ["--env-file", ".env"] : [];

function run(args) {
  const result = spawnSync(composeBin, [...baseArgs, ...envFileArgs, ...args], {
    stdio: "inherit",
    env: process.env,
  });
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
      run(["up", "-d", "--no-build"]);
    } else {
      run(["up", "--build", "-d"]);
    }
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
