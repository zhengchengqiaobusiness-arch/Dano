#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import {
  existsSync, mkdirSync, readFileSync, readdirSync, rmSync,
  writeFileSync, renameSync,
} from "node:fs";
import { join, resolve } from "node:path";
import { resolveProductName } from "../apps/dano/runtime/product-name.mjs";
import { readProductNameOverlay } from "./deploy-product-identity.mjs";
import { acquireDeploymentLock } from "./deploy-lock.mjs";

// Compose consumes secrets; this process never reads .env or resolved config.
const deployDir = resolve(process.env.DANO_DEPLOY_DIR || process.cwd());
const overlayPath = join(deployDir, "docker-compose.product-name.json");
const composeBin = process.env.DANO_COMPOSE || "docker";
const files = process.env.DANO_DEPLOY_COMPOSE_FILES
  ? JSON.parse(process.env.DANO_DEPLOY_COMPOSE_FILES)
  : ["docker-compose.yml", "docker-compose.exposure.yml"];
const [action = "repair", value, ...extra] = process.argv.slice(2);
const disposition = ["accept", "rollback", "abort"].includes(action);
if (!Array.isArray(files) || !files.length || !files.every(file => typeof file === "string") ||
    !["repair", "set-name", "accept", "rollback", "abort"].includes(action) || extra.length ||
    (action === "set-name" && (!value?.trim() || value.includes("{产品名称}"))) ||
    (action === "repair" && value) || (disposition && !/^[a-f0-9-]{36}$/.test(value || ""))) {
  throw new Error("Usage: deploy-system-prompt.mjs repair | set-name <name> | accept <transaction> | rollback <transaction> | abort <transaction>");
}
if (action === "set-name") resolveProductName(value, undefined);
const transaction = disposition ? value : randomUUID();
const rollbackDir = join(deployDir, `.system-rollback-${transaction}`);
const healthBase = new URL(process.env.DANO_SMOKE_BASE_URL || "http://127.0.0.1");
if (!["http:", "https:"].includes(healthBase.protocol) || healthBase.username || healthBase.password) throw new Error("HEALTH_URL_INVALID");
const healthUrl = new URL("/api/health", healthBase);
function compose(args) {
  const result = spawnSync(composeBin, ["compose", ...files.flatMap(file => ["-f", file]),
    ...(existsSync(overlayPath) ? ["-f", overlayPath] : []), "--env-file", ".env", ...args], {
    cwd: deployDir, env: process.env, encoding: "utf8", maxBuffer: 1024 * 1024,
  });
  if (result.error || result.status !== 0) throw new Error("COMPOSE_STEP_FAILED");
}
function prompt(command) {
  compose(["run", "--rm", "--no-deps", "--entrypoint", "node", "app",
    "./deploy/system-prompt.mjs", command, transaction]);
}
async function routedHealth() {
  const response = await fetch(healthUrl, { redirect: "error", signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error("INGRESS_HEALTH_FAILED");
  await response.body?.cancel();
}
async function restartAndCheck(recreate) {
  compose(recreate
    ? ["up", "-d", "--no-build", "--no-deps", "--force-recreate", "app"]
    : ["restart", "app"]);
  compose(["exec", "-T", "app", "node", "--input-type=module", "-e",
    "for(let i=0;i<60;i++){try{const r=await fetch('http://127.0.0.1:8080/api/health');if(r.ok)process.exit(0)}catch{}await new Promise(r=>setTimeout(r,1000))}process.exit(1)"]);
  if (recreate) {
    // The shipped nginx upstream resolves app's IP at configuration load time.
    compose(["exec", "-T", "nginx", "nginx", "-s", "reload"]);
  }
  // A reload is asynchronous. Wait for the ingress, not just container health.
  for (let attempt = 0; attempt < 20; attempt++) {
    try { await routedHealth(); return; } catch { /* bounded retry */ }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error("INGRESS_HEALTH_FAILED");
}
function publishOverlay(content) {
  const temporary = `${overlayPath}.${transaction}`;
  try {
    writeFileSync(temporary, content, { mode: 0o600, flag: "wx", flush: true });
    renameSync(temporary, overlayPath);
  } finally { rmSync(temporary, { force: true }); }
}
function readManagedOverlay() {
  if (!existsSync(overlayPath)) return undefined;
  readProductNameOverlay(overlayPath);
  return readFileSync(overlayPath);
}
function discard() {
  prompt("discard");
  rmSync(rollbackDir, { recursive: true });
}
async function rollback(metadata, restart) {
  if (metadata.overlayExisted) publishOverlay(readFileSync(join(rollbackDir, "product-name.json")));
  else rmSync(overlayPath, { force: true });
  prompt("restore");
  if (restart) await restartAndCheck(metadata.recreate);
  discard();
}
let unlock;
let backedUp = false;
let changed = false;
let metadata;
let stage = "lock";
try {
  unlock = acquireDeploymentLock();
  if (disposition) {
    metadata = JSON.parse(readFileSync(join(rollbackDir, "transaction.json"), "utf8"));
    if (metadata.transaction !== transaction) throw new Error("TRANSACTION_INVALID");
    stage = action;
    if (action === "abort") {
      if (metadata.phase !== "preparing") throw new Error("TRANSACTION_ALREADY_MUTATED");
      discard();
    } else if (action === "rollback") await rollback(metadata, true);
    else {
      await routedHealth();
      compose(["exec", "-T", "app", "node", "./deploy/system-prompt.mjs", "check"]);
      discard();
    }
    console.log(`[deploy-system-prompt] ${action}: PASS`);
  } else {
    stage = "preflight";
    if (readdirSync(deployDir).some(name => name.startsWith(".system-rollback-"))) throw new Error("PENDING_TRANSACTION");
    await routedHealth();
    compose(["exec", "-T", "app", "node", "-e",
      "fetch('http://127.0.0.1:8080/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]);
    const oldOverlay = readManagedOverlay();
    metadata = { transaction, phase: "preparing", overlayExisted: Boolean(oldOverlay), recreate: action === "set-name" };
    mkdirSync(rollbackDir, { mode: 0o700 });
    writeFileSync(join(rollbackDir, "transaction.json"), JSON.stringify(metadata), { mode: 0o600, flush: true });
    if (oldOverlay) writeFileSync(join(rollbackDir, "product-name.json"), oldOverlay, { mode: 0o600, flush: true });
    stage = "backup";
    prompt("backup");
    backedUp = true;
    metadata.phase = "prepared";
    writeFileSync(join(rollbackDir, "transaction.json"), JSON.stringify(metadata), { mode: 0o600, flush: true });
    if (action === "set-name") {
      publishOverlay(JSON.stringify({ services: { app: { environment: {
        DANO_PRODUCT_NAME: value.trim().replaceAll("$", () => "$$"),
      } } } }));
    }
    stage = "sync";
    prompt("sync");
    prompt("check");
    changed = true;
    stage = "restart";
    await restartAndCheck(metadata.recreate);
    compose(["exec", "-T", "app", "node", "./deploy/system-prompt.mjs", "check"]);
    console.log(`[deploy-system-prompt] pending browser acceptance: ${transaction}; retain rollback until accept or rollback`);
  }
} catch {
  if (backedUp) {
    try {
      await rollback(metadata, changed);
      console.error("[deploy-system-prompt] failed; rolled back; app healthy");
    } catch {
      console.error(`[deploy-system-prompt] rollback failed; retain transaction ${transaction} for recovery under deployment lock`);
    }
  } else {
    if (!disposition && metadata?.phase === "preparing") {
      try { discard(); } catch {
        console.error(`[deploy-system-prompt] incomplete preparation ${transaction}; retry abort under deployment lock`);
      }
    }
    console.error(`[deploy-system-prompt] ${stage} failed; no app switch`);
  }
  process.exitCode = 1;
} finally { unlock?.(); }
