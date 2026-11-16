import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, isAbsolute } from "node:path";

export const productionProfile = JSON.parse(readFileSync(new URL("../deploy/production/profile.json", import.meta.url), "utf8"));
export function productionSshArguments(profile = productionProfile) {
  if (!/^[a-zA-Z0-9_.@-]+$/.test(profile.sshHost) || isAbsolute(profile.sshIdentity) || profile.sshIdentity.includes(".."))
    throw new Error("INVALID_PRODUCTION_SSH_PROFILE");
  return ["-i", join(homedir(), profile.sshIdentity), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=15",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", profile.sshHost];
}

export function quoteRemoteArgument(value) {
  return "'" + value.replaceAll("'", "'\\''") + "'";
}

export function validateProductionChecks(pages, policy, hasWorkflows) {
  const checks = pages.flatMap(page => page.check_runs);
  const required = policy.requiredChecks;
  if (!Array.isArray(required) || (hasWorkflows && required.length === 0)) throw new Error("REQUIRED_CHECKS_NOT_CONFIGURED");
  if (checks.some(check => check.status !== "completed" || !["success", "neutral", "skipped"].includes(check.conclusion))
    || required.some(name => !checks.some(check => check.name === name && check.status === "completed" && check.conclusion === "success"))
    || (!checks.length && !(policy.allowNoChecks === true && !hasWorkflows && !required.length)))
    throw new Error("UPSTREAM_CHECKS_NOT_PASSED");
  return { status: checks.length ? "passed" : "not_configured", checkCount: checks.length, requiredChecks: required };
}
