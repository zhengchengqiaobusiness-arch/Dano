import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, isAbsolute } from "node:path";

export const profileErrors = [
  "DANO_PRODUCTION_PROFILE_REQUIRED", "INVALID_PRODUCTION_PROFILE_PATH",
  "PRODUCTION_PROFILE_UNREADABLE", "INVALID_PRODUCTION_SSH_PROFILE",
  "INVALID_PRODUCTION_BASE_URL",
];

export function loadProductionProfile(path = process.env.DANO_PRODUCTION_PROFILE) {
  if (!path) throw new Error("DANO_PRODUCTION_PROFILE_REQUIRED");
  if (!isAbsolute(path)) throw new Error("INVALID_PRODUCTION_PROFILE_PATH");
  let profile;
  try {
    profile = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new Error("PRODUCTION_PROFILE_UNREADABLE");
  }
  productionSshArguments(profile);
  if (typeof profile.baseUrl !== "string") throw new Error("INVALID_PRODUCTION_BASE_URL");
  let baseUrl;
  try { baseUrl = new URL(profile.baseUrl); } catch { throw new Error("INVALID_PRODUCTION_BASE_URL"); }
  if (baseUrl.protocol !== "https:" || baseUrl.href !== baseUrl.origin + "/")
    throw new Error("INVALID_PRODUCTION_BASE_URL");
  return { ...profile, baseUrl: baseUrl.href };
}

export function productionSshArguments(profile) {
  if (typeof profile?.sshHost !== "string" || !/^(?:[a-zA-Z0-9_][a-zA-Z0-9_.-]*@)?[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(profile.sshHost)
    || typeof profile.sshIdentity !== "string" || !profile.sshIdentity || isAbsolute(profile.sshIdentity) || profile.sshIdentity.includes(".."))
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
