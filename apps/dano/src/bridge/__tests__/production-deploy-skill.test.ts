import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

const skill = new URL("../../../../../.agents/skills/dano-production-deploy/scripts/", import.meta.url);
const since = "2026-10-10T08:00:00Z";
function logs(input: string, readiness?: string, extra: NodeJS.ProcessEnv = {}) {
  return spawnSync(process.execPath, [new URL("summarize-logs.mjs", skill).pathname], {
    input, encoding: "utf8",
    env: { ...process.env, DANO_DIAGNOSTIC_SINCE: since, DANO_DIAGNOSTIC_READINESS: readiness || "", ...extra },
  });
}
const line = (body: string, time = "08:00:01", service = "nginx-1") => `${service} | 2026-10-10T${time}Z ${body}\n`;
const access = (status: number, bytes = 503) => `192.0.2.1 - - [10/Oct/2026:08:00:01 +0000] "GET /assets/500.js HTTP/1.1" ${status} ${bytes} "-" "synthetic-agent"`;
const temporary: string[] = [];
afterEach(() => temporary.splice(0).forEach(path => rmSync(path, { recursive: true, force: true })));

// Replace only the SSH transport: the bootstrap, checksum, syntax gate and
// payload run through real Bash/filesystem operations in disposable directories.
function remoteFixture(script: string) {
  const root = mkdtempSync(join(tmpdir(), "dano-remote-test-")); temporary.push(root);
  const bin = join(root, "bin"); mkdirSync(bin);
  const calls = join(root, "calls.jsonl");
  const payload = join(root, "payload.sh"); writeFileSync(payload, script);
  const executable = (name: string, source: string) => {
    const path = join(bin, name); writeFileSync(path, `#!${process.execPath}\n${source}`); chmodSync(path, 0o755);
  };
  executable("sha256sum", `const fs=require('node:fs'),crypto=require('node:crypto');
const digest=process.env.TEST_CORRUPT_DIGEST==='1'?'0'.repeat(64):crypto.createHash('sha256').update(fs.readFileSync(process.argv[2])).digest('hex');
console.log(digest+'  '+process.argv[2]);`);
  writeFileSync(join(bin, "flock"), '#!/bin/bash\nif test "$TEST_LOCK_BUSY" = 1; then exit 1; fi\nexit 0\n'); chmodSync(join(bin, "flock"), 0o755);
  executable("ssh", `const fs=require('node:fs'),cp=require('node:child_process');
fs.appendFileSync(process.env.TEST_SSH_CALLS,JSON.stringify(process.argv.slice(2))+'\\n');
if(process.env.TEST_SSH_FAIL==='execution'&&process.argv.at(-1).includes('digest=')){process.stdout.write('never-print-this');process.exit(255);}
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',s=>input+=s);
process.stdin.on('end',()=>{const command=process.argv.at(-1).replaceAll('/var/lock/dano-production-deploy.lock',process.env.TEST_LOCK_PATH);const result=cp.spawnSync('/bin/bash',['-c',command],{input,encoding:'utf8',env:process.env});process.stdout.write(result.stdout||'');process.stderr.write(result.stderr||'');process.exit(result.status??1);});`);
  const run = (kind = "diagnostic", extra: NodeJS.ProcessEnv = {}) => {
    const result = spawnSync(process.execPath, [new URL("run-remote-script.mjs", skill).pathname, kind, payload], {
      encoding: "utf8", env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, TEST_SSH_CALLS: calls, TEST_LOCK_PATH: join(root, "release.lock"), ...extra }, timeout: 10000,
    });
    const receipts = result.stdout.trim().split("\n").filter(Boolean).map(row => JSON.parse(row));
    for (const receipt of receipts) if (receipt.runDirectory) temporary.push(receipt.runDirectory);
    return { ...result, receipts, calls: existsSync(calls) ? readFileSync(calls, "utf8").trim().split("\n").map(row => JSON.parse(row)) : [] };
  };
  return { root, payload, run };
}

describe("production deployment log evidence", () => {
  it("counts HTTP status rather than response bytes, paths or arbitrary numbers", () => {
    const result = logs(line(access(200)) + line("API_KEY=never-print-this latency=502", "08:00:02", "app-1"));
    expect(result.status).toBe(0);
    expect(JSON.parse(result.stdout).categories.http5xx).toBe(0);
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
  });
  it("keeps switching errors separate from post-readiness failures", () => {
    const input = line(access(502))
      + line("[error] 1#1: connect() failed (111: Connection refused) while connecting to upstream")
      + line(access(503), "08:00:05")
      + line("[error] 1#1: connect() failed (111: Connection refused) while connecting to upstream", "08:00:05");
    const result = logs(input, "2026-10-10T08:00:04Z");
    expect(result.status).toBe(0);
    const summary = JSON.parse(result.stdout);
    expect(summary.windows.beforeReadiness.categories.http5xx).toBe(1);
    expect(summary.windows.beforeReadiness.reasons.upstream_connection_refused_before_ready).toBe(1);
    expect(summary.windows.afterReadiness.categories.http5xx).toBe(1);
    expect(summary.windows.afterReadiness.unclassified.errors).toBe(1);
  });
  it("distinguishes timeout configuration and nginx buffering from unknown incidents", () => {
    const input = line("  timeout: 30000,", "08:00:01", "app-1")
      + line("[warn] 1#1: an upstream response is buffered to a temporary file", "08:00:05")
      + line("request timed out: never-print-this", "08:00:06", "app-1");
    const result = logs(input, "2026-10-10T08:00:04Z");
    const summary = JSON.parse(result.stdout);
    expect(summary.categories.timeouts).toBe(1);
    expect(summary.reasons.timeout_configuration).toBe(1);
    expect(summary.windows.afterReadiness.reasons.nginx_temporary_buffer).toBe(1);
    expect(summary.windows.afterReadiness.unclassified.timeouts).toBe(1);
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
  });
  it("uses structured severity and HTTP fields without interpreting user text", () => {
    const input = line(JSON.stringify({ level: "info", message: "user said error warning 503 never-print-this", statusCode: 200, bytes: 502 }), "08:00:01", "app-1")
      + line(JSON.stringify({ level: "error", message: "request timed out", statusCode: 504 }), "08:00:05", "app-1");
    const result = logs(input, "2026-10-10T08:00:04Z");
    const summary = JSON.parse(result.stdout);
    expect(summary.categories.errors).toBe(1);
    expect(summary.categories.warnings).toBe(0);
    expect(summary.categories.http5xx).toBe(1);
    expect(summary.windows.afterReadiness.unclassified.timeouts).toBe(1);
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
  });
  it("rejects unscoped and truncated evidence rather than calling it clean", () => {
    const input = "app-1 | user supplied 2026-10-10T08:00:01Z\n" + line(access(200)) + line(access(500));
    const result = logs(input, undefined, { DANO_DIAGNOSTIC_MAX_LINES: "1" });
    expect(result.status).toBe(2);
    expect(JSON.parse(result.stdout)).toMatchObject({ unscopedLines: 1, truncated: true, analyzedLines: 1 });
  });
  it.each([
    { DANO_DIAGNOSTIC_SINCE: "never-print-this" },
    { DANO_DIAGNOSTIC_READINESS: "2026-10-10T07:59:59Z" },
    { DANO_DIAGNOSTIC_READINESS: "October 10, 2026" },
  ])("rejects invalid diagnostic windows without echoing input", extra => {
    const result = logs(line(access(200)), undefined, extra);
    expect(result.status).toBe(2);
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
  });
  it("reports an empty window explicitly", () => {
    const result = logs("");
    expect(JSON.parse(result.stdout)).toMatchObject({ emptyWindow: true, analyzedLines: 0 });
  });
  it("keeps independent service counts for names that overlap object properties", () => {
    const result = logs(line(access(503), "08:00:01", "constructor") + line(access(200), "08:00:02", "__proto__"));
    expect(result.status).toBe(0);
    const summary = JSON.parse(result.stdout);
    expect(summary.services.constructor.categories.http5xx).toBe(1);
    expect(summary.services.__proto__.categories.http5xx).toBe(0);
  });
  it("retains unknown failures when a structured level is malformed", () => {
    const result = logs(line('{"level":"unknown","message":"request failed"}', "08:00:01", "app-1"));
    expect(JSON.parse(result.stdout).unclassified.errors).toBe(1);
  });
  it("does not let a known warning hide an additional unexplained timeout", () => {
    const result = logs(line("[warn] an upstream response is buffered to a temporary file; request timed out"));
    const summary = JSON.parse(result.stdout);
    expect(summary.reasons.nginx_temporary_buffer).toBe(1);
    expect(summary.unclassified.warnings).toBe(0);
    expect(summary.unclassified.timeouts).toBe(1);
  });
});

describe("production deployment host capability probe", () => {
  it("reports old CLI support without exposing errors or requiring optional tools", () => {
    const root = mkdtempSync(join(tmpdir(), "dano-capability-test-")); temporary.push(root);
    const git = join(root, "git");
    writeFileSync(git, '#!/bin/bash\nif test "$1" = symbolic-ref; then printf main; exit 0; fi\nprintf never-print-this >&2\nexit 129\n'); chmodSync(git, 0o755);
    const docker = join(root, "docker");
    writeFileSync(docker, '#!/bin/bash\nif test "$2" = version; then exit 0; fi\nprintf never-print-this >&2\nexit 1\n'); chmodSync(docker, 0o755);
    const result = spawnSync("bash", [new URL("probe-host.sh", skill).pathname, root], {
      encoding: "utf8", env: { ...process.env, PATH: `${root}:${process.env.PATH}` },
    });
    expect(result.status).toBe(0);
    expect(JSON.parse(result.stdout)).toMatchObject({ gitC: false, gitShowCurrent: false, gitSymbolicRef: true, compose: true, composeInteractiveFlag: false, buildxFormat: false });
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
  });
});

describe("production deployment remote script execution", () => {
  it("transfers a long script intact and keeps child output private", () => {
    const f = remoteFixture(`# ${"x".repeat(16000)}\nprintf 'never-print-this\\n'\nprintf 'remote-executed' > "$TEST_EXECUTED"\n`);
    const executed = join(f.root, "executed");
    const result = f.run("diagnostic", { TEST_EXECUTED: executed });
    expect(result.status, result.stderr).toBe(0);
    expect(readFileSync(executed, "utf8")).toBe("remote-executed");
    expect(result.receipts.at(-1)).toMatchObject({ kind: "diagnostic", exitCode: 0, disposition: "passed" });
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
    expect(result.calls).toHaveLength(2);
    for (const call of result.calls) expect(call.slice(0, 7)).toEqual(["-i", join(process.env.HOME!, ".ssh/id_rsa"), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "root@1.15.173.22"]);
  });
  it("records a failed diagnostic without running any rollback or forwarding raw errors", () => {
    const f = remoteFixture("printf 'never-print-this' >&2\nexit 7\n");
    const result = f.run();
    expect(result.status).toBe(0);
    expect(result.receipts.at(-1)).toMatchObject({ stage: "completed", exitCode: 7, disposition: "retry-diagnostic" });
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
    expect(readFileSync(join(result.receipts.at(-1).runDirectory, "stderr.log"), "utf8")).toBe("never-print-this");
    expect(result.calls).toHaveLength(2);
  });
  it.each(["mutation", "acceptance"])("requires classification for a failed %s step", kind => {
    const result = remoteFixture("exit 9\n").run(kind);
    expect(result.status).toBe(0);
    expect(result.receipts.at(-1)).toMatchObject({ kind, exitCode: 9, disposition: "stop-and-classify" });
  });
  it("rejects syntax errors before making any SSH connection", () => {
    const result = remoteFixture("if then never-print-this\n").run();
    expect(result.status).toBe(2);
    expect(result.calls).toHaveLength(0);
    expect(result.receipts.at(-1)).toMatchObject({ error: "LOCAL_SYNTAX_FAILED" });
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
  });
  it("rejects changed upload bytes before executing the script", () => {
    const f = remoteFixture("touch \"$TEST_EXECUTED\"\n");
    const executed = join(f.root, "executed");
    const result = f.run("mutation", { TEST_CORRUPT_DIGEST: "1", TEST_EXECUTED: executed });
    expect(result.status).toBe(2);
    expect(existsSync(executed)).toBe(false);
    expect(result.receipts.at(-1)).toMatchObject({ execution: "unconfirmed", disposition: "inspect-before-mutation" });
  });
  it("fails closed before a mutation when another deployment owns the lock", () => {
    const f = remoteFixture("touch \"$TEST_EXECUTED\"\n");
    const executed = join(f.root, "executed");
    const result = f.run("mutation", { TEST_LOCK_BUSY: "1", TEST_EXECUTED: executed });
    expect(result.status).toBe(2);
    expect(existsSync(executed)).toBe(false);
    expect(result.receipts.at(-1)).toMatchObject({ disposition: "inspect-before-mutation" });
  });
  it("reports interrupted transport as unconfirmed without exposing transport output", () => {
    const result = remoteFixture("exit 0\n").run("diagnostic", { TEST_SSH_FAIL: "execution" });
    expect(result.status).toBe(2);
    expect(result.receipts.at(-1)).toMatchObject({ execution: "unconfirmed", error: "SSH_EXECUTION_UNCONFIRMED" });
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
  });
  it("rejects an unknown step kind without echoing it or connecting", () => {
    const result = remoteFixture("exit 0\n").run("never-print-this");
    expect(result.status).toBe(2);
    expect(result.calls).toHaveLength(0);
    expect(result.stdout + result.stderr).not.toContain("never-print-this");
  });
});
