#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { productionProfile, productionSshArguments, quoteRemoteArgument, validateProductionChecks } from "./production-profile.mjs";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const args = process.argv.slice(2);
const image = args[0] === "--image" ? args[1] : undefined;
const plan = args[2] === "--plan";
if (!image || !/^[a-zA-Z0-9][a-zA-Z0-9./:_@-]+$/.test(image) || (args.length !== 2 && !(args.length === 3 && plan))) {
  console.error("Usage: pnpm deploy:production --image <prepared-image-tag-or-digest> [--plan]");
  process.exit(1);
}
let temporary;
function output(command, argv, timeout = 120_000) {
  const result = spawnSync(command, argv, { cwd: root, encoding: "utf8", env: { ...process.env, GIT_TERMINAL_PROMPT: "0" }, timeout, maxBuffer: 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error("DEPLOY_COMMAND_FAILED");
  return result.stdout.trim();
}
try {
  if (plan) {
    console.log(JSON.stringify({ status: "plan", host: productionProfile.sshHost, image,
      stages: ["image-and-topology-preflight", "backup-and-system-sync", "switch-app", "health", "nginx-reload", "smoke", "receipt-and-unlock"],
      buildsImage: false, downloadsDependencies: false, browserSmoke: "required", browserAcceptance: "not_requested" }));
  } else {
    if (output("git", ["status", "--porcelain"]) || output("git", ["branch", "--show-current"]) !== "main") throw new Error("CLEAN_MAIN_REQUIRED");
    const loadedHead = output("git", ["rev-parse", "HEAD"]);
    output("git", ["sync-upstream"]);
    if (output("git", ["rev-parse", "HEAD"]) !== loadedHead) {
      if (process.env.DANO_DEPLOY_REEXECUTED === "1") throw new Error("UPSTREAM_ADVANCED");
      // Reload executable code, profile and CI policy from one synchronized tree.
      const refreshed = spawnSync(process.execPath, [fileURLToPath(import.meta.url), ...args], {
        cwd: root, stdio: "inherit", env: { ...process.env, DANO_DEPLOY_REEXECUTED: "1" },
      });
      process.exit(refreshed.status ?? 1);
    }
    if (output("git", ["status", "--porcelain"])) throw new Error("CLEAN_MAIN_REQUIRED");
    const targetSha = output("git", ["rev-parse", "upstream/main"]);
    if (!/^[a-f0-9]{40}$/.test(targetSha) || ["main", "origin/main"].some(ref => output("git", ["rev-parse", ref]) !== targetSha))
      throw new Error("MAIN_NOT_SYNCHRONIZED");
    const upstream = output("git", ["ls-remote", "upstream", "refs/heads/main"], 60_000).split(/\s+/)[0];
    if (upstream !== targetSha) throw new Error("UPSTREAM_ADVANCED");
    const pages = JSON.parse(output(productionProfile.githubCli, ["api", "--paginate", "--slurp",
      `repos/${productionProfile.githubRepository}/commits/${targetSha}/check-runs?per_page=100`]));
    const hasWorkflows = output("git", ["ls-tree", "-r", "--name-only", targetSha, ".github/workflows"]).split("\n").some(path => /\.ya?ml$/.test(path));
    const ci = validateProductionChecks(pages, productionProfile.ci, hasWorkflows);
    const version = JSON.parse(output("git", ["show", `${targetSha}:package.json`])).version;
    const attemptId = randomUUID();
    const manifest = { targetSha, version, image, attemptId, checkedAt: new Date().toISOString(), ci, profile: productionProfile };
    temporary = mkdtempSync(join(tmpdir(), "dano-production-release-"));
    const encode = value => Buffer.from(value).toString("base64");
    const files = {
      "manifest.json": JSON.stringify(manifest),
      "release.py": readFileSync(join(root, "deploy/production/release.py"), "utf8"),
      "compose-filter.mjs": readFileSync(join(root, ".agents/skills/dano-production-deploy/scripts/summarize-compose-config.mjs"), "utf8"),
      "smoke.mjs": readFileSync(join(root, "scripts/smoke-dano-deploy.mjs"), "utf8"),
    };
    const script = ["#!/bin/bash", "set -euo pipefail", "umask 077", 'work=$(mktemp -d /tmp/dano-production-release.XXXXXX)'];
    for (const [name, content] of Object.entries(files)) script.push(`printf %s '${encode(content)}' | base64 -d > "$work/${name}"`);
    script.push("trap 'rm -rf \"$work\"' EXIT");
    script.push('python3 "$work/release.py" "$work/manifest.json" "$work"');
    const payload = join(temporary, "release.sh"); writeFileSync(payload, script.join("\n") + "\n", { mode: 0o600 });
    const receipt = output(process.execPath, [join(root, ".agents/skills/dano-production-deploy/scripts/run-remote-script.mjs"), "mutation", payload], 0)
      .split("\n").filter(Boolean).map(line => JSON.parse(line)).at(-1);
    if (receipt?.stage !== "completed") throw new Error("REMOTE_DEPLOYMENT_INCOMPLETE");
    const receiptPath = `${productionProfile.receiptDirectory}/${attemptId}/receipt.json`;
    const remote = JSON.parse(output("ssh", [...productionSshArguments(), `cat -- ${quoteRemoteArgument(receiptPath)}`]));
    if (remote.attemptId !== attemptId || remote.targetSha !== targetSha || !["deployed", "rolled_back", "failed_before_switch", "rollback_failed"].includes(remote.status)) throw new Error("INVALID_DEPLOYMENT_RECEIPT");
    console.log(JSON.stringify(remote));
    if (remote.status !== "deployed" || receipt.exitCode !== 0) process.exitCode = 1;
  }
} catch (error) {
  const allowed = ["REQUIRED_CHECKS_NOT_CONFIGURED", "UPSTREAM_CHECKS_NOT_PASSED", "CLEAN_MAIN_REQUIRED", "MAIN_NOT_SYNCHRONIZED", "UPSTREAM_ADVANCED", "INVALID_DEPLOYMENT_RECEIPT", "REMOTE_DEPLOYMENT_INCOMPLETE"];
  console.error(allowed.includes(error.message) ? error.message : "PRODUCTION_DEPLOY_FAILED: inspect the private host receipt/logs; no browser acceptance was started.");
  process.exitCode = 1;
} finally {
  if (temporary) rmSync(temporary, { recursive: true, force: true });
}
