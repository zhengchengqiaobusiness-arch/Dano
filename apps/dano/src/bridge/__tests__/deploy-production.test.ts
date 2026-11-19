import { chmodSync, copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { afterEach, expect, it } from "vitest";

const root = fileURLToPath(new URL("../../../../../", import.meta.url));
const cli = fileURLToPath(new URL("../../../../../scripts/deploy-production.mjs", import.meta.url));
const fixtures: string[] = [];
afterEach(() => fixtures.splice(0).forEach(path => rmSync(path, { recursive: true, force: true })));
const target = { sshHost: "deploy@ssh.test", sshIdentity: ".ssh/test_key", baseUrl: "https://public.test/" };

function plan(profile?: object) {
  let profileFile = "";
  if (profile) {
    const directory = mkdtempSync(join(tmpdir(), "dano-production-plan-")); fixtures.push(directory);
    profileFile = join(directory, "profile.json");
    writeFileSync(profileFile, JSON.stringify(profile));
  }
  return spawnSync(process.execPath, [cli, "--image", "dano-app:test", "--plan"], {
    encoding: "utf8", env: { ...process.env, PATH: "/nonexistent", DANO_PRODUCTION_PROFILE: profileFile },
  });
}

it("plans the configured deployment without Git, build, remote mutation or acceptance", () => {
  const result = plan(target);
  expect(result.status, result.stderr).toBe(0);
  expect(JSON.parse(result.stdout)).toMatchObject({ status: "plan", host: target.sshHost, baseUrl: target.baseUrl, buildsImage: false, downloadsDependencies: false, browserSmoke: "required", browserAcceptance: "not_requested" });
  expect(JSON.parse(result.stdout).stages).toContain("publish-question-guide");
});
it("requires an external profile with no fallback target", () => {
  const result = plan();
  expect(result.status).toBe(1);
  expect(result.stderr.trim()).toBe("DANO_PRODUCTION_PROFILE_REQUIRED");
});
it.each(["", "http://public.test/", "https://name:secret@public.test/", "https://public.test/web/", "https://public.test/?q=secret"])("rejects an invalid public base URL", baseUrl => {
  const result = plan({ ...target, baseUrl });
  expect(result.status).toBe(1);
  expect(result.stderr.trim()).toBe("INVALID_PRODUCTION_BASE_URL");
});
it("rejects an SSH target that could be interpreted as an option", () => {
  const result = plan({ ...target, sshHost: "-oProxyCommand" });
  expect(result.status).toBe(1);
  expect(result.stderr.trim()).toBe("INVALID_PRODUCTION_SSH_PROFILE");
});
it("rejects a shell-shaped image argument before any remote action", () => {
  const result = spawnSync(process.execPath, [cli, "--image", "$(echo invalid)"], { encoding: "utf8" });
  expect(result.status).toBe(1);
});
it("runs deployment transaction regression coverage with Python's standard library", () => {
  const result = spawnSync("python3", ["-m", "unittest", "discover", "-s", "deploy/production", "-p", "test_*.py"], {
    cwd: root, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  });
  expect(result.status, result.stdout + result.stderr).toBe(0);
  expect(result.stderr).toMatch(/Ran \d+ tests/);
}, 30_000);

it("quotes remote paths as literal shell arguments", async () => {
  // @ts-expect-error Public deployment module has no TypeScript declaration.
  const { quoteRemoteArgument } = await import("../../../../../scripts/production-profile.mjs");
  const value = "/tmp/a'b $(printf unexpected)";
  const result = spawnSync("bash", ["-c", `printf %s ${quoteRemoteArgument(value)}`], { encoding: "utf8" });
  expect(result.status).toBe(0);
  expect(result.stdout).toBe(value);
});

it("requires declared CI evidence, checks every page and explicitly records repositories without CI", async () => {
  // @ts-expect-error Public deployment module has no TypeScript declaration.
  const { validateProductionChecks } = await import("../../../../../scripts/production-profile.mjs");
  const success = { name: "required", status: "completed", conclusion: "success" };
  const policy = { requiredChecks: ["required"], allowNoChecks: false };
  expect(() => validateProductionChecks([{ check_runs: [] }], policy, true)).toThrow("UPSTREAM_CHECKS_NOT_PASSED");
  expect(() => validateProductionChecks([{ check_runs: [success] }, { check_runs: [{ ...success, name: "later", conclusion: "failure" }] }], policy, true)).toThrow("UPSTREAM_CHECKS_NOT_PASSED");
  expect(() => validateProductionChecks([{ check_runs: [] }], { requiredChecks: [], allowNoChecks: true }, true)).toThrow("REQUIRED_CHECKS_NOT_CONFIGURED");
  expect(validateProductionChecks([{ check_runs: [] }], { requiredChecks: [], allowNoChecks: true }, false)).toMatchObject({ status: "not_configured", checkCount: 0 });
});

it("revalidates the external profile and required checks after reexecuting synced code", () => {
  const directory = mkdtempSync(join(tmpdir(), "dano-production-sync-")); fixtures.push(directory);
  const bin = join(directory, "bin"); mkdirSync(bin);
  mkdirSync(join(directory, "scripts"));
  const profileFile = join(directory, "profile.json");
  const profile = { ...JSON.parse(readFileSync(join(root, "deploy/production/profile.example.json"), "utf8")), ...target };
  profile.githubCli = join(bin, "gh");
  writeFileSync(profileFile, JSON.stringify(profile));
  for (const file of ["deploy-production.mjs", "production-profile.mjs"]) copyFileSync(join(root, "scripts", file), join(directory, "scripts", file));
  const state = join(directory, "synced");
  const executable = (name: string, source: string) => {
    const path = join(bin, name); writeFileSync(path, `#!${process.execPath}\n${source}`); chmodSync(path, 0o755);
  };
  executable("gh", "console.log(JSON.stringify([{check_runs:[]}]));");
  executable("git", `const fs=require('node:fs');const args=process.argv.slice(2);const state=${JSON.stringify(state)};const profileFile=${JSON.stringify(profileFile)};
if(args[0]==='status')process.exit(0);
if(args[0]==='branch'){console.log('main');process.exit(0);}
if(args[0]==='sync-upstream'){fs.writeFileSync(state,'synced');const p=JSON.parse(fs.readFileSync(profileFile));p.ci.requiredChecks=['new-required-check'];fs.writeFileSync(profileFile,JSON.stringify(p));process.exit(0);}
if(args[0]==='rev-parse'){console.log((fs.existsSync(state)?'b':'a').repeat(40));process.exit(0);}
if(args[0]==='ls-remote'){console.log('b'.repeat(40)+' refs/heads/main');process.exit(0);}
if(args[0]==='ls-tree')process.exit(0);
if(args[0]==='show'){console.log(JSON.stringify({version:'1.2.3'}));process.exit(0);}
process.exit(2);`);
  const result = spawnSync(process.execPath, [join(directory, "scripts/deploy-production.mjs"), "--image", "prepared-image"], {
    encoding: "utf8", env: { ...process.env, PATH: bin, DANO_DEPLOY_REEXECUTED: "0", DANO_PRODUCTION_PROFILE: profileFile },
  });
  expect(result.status).toBe(1);
  expect(result.stderr).toContain("UPSTREAM_CHECKS_NOT_PASSED");
});
