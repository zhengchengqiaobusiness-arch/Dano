#!/usr/bin/env node
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { lstatSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const [kind, scriptPath, ...extra] = process.argv.slice(2);
const kinds = ["diagnostic", "mutation", "acceptance"];
let runDirectory;
let scriptSha256;
const emit = value => console.log(JSON.stringify({ kind: kinds.includes(kind) ? kind : undefined, scriptSha256, runDirectory, ...value }));

// SSH receives a file through a pipe, never through a canonical terminal.
// Child output remains on the host; only validated transport receipts leave it.
function ssh(command, input = "", timeoutMs) {
  return new Promise(resolve => {
    const child = spawn("ssh", ["-i", join(homedir(), ".ssh/id_rsa"),
      "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=15",
      "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "root@1.15.173.22", command],
    { stdio: ["pipe", "pipe", "pipe"] });
    let output = "";
    let overflow = false;
    let failed = false;
    child.stdout.on("data", chunk => {
      if (Buffer.byteLength(output) + chunk.length > 65536) overflow = true;
      if (!overflow) output += chunk.toString();
    });
    child.stderr.resume();
    child.on("error", () => { failed = true; });
    child.stdin.on("error", () => {});
    child.stdin.end(input);
    const timer = timeoutMs ? setTimeout(() => child.kill("SIGTERM"), timeoutMs) : undefined;
    child.on("close", status => {
      if (timer) clearTimeout(timer);
      resolve({ status, output, failed: failed || overflow });
    });
  });
}

try {
  if (!kinds.includes(kind) || !scriptPath || extra.length) throw new Error("INVALID_ARGUMENTS");
  if (!lstatSync(scriptPath).isFile() || lstatSync(scriptPath).isSymbolicLink()) throw new Error("INVALID_SCRIPT");
  const script = readFileSync(scriptPath);
  if (!script.length || script.length > 1024 * 1024) throw new Error("INVALID_SCRIPT");
  const syntax = spawnSync("bash", ["-n"], { input: script, stdio: ["pipe", "ignore", "ignore"] });
  if (syntax.error || syntax.status !== 0) throw new Error("LOCAL_SYNTAX_FAILED");
  scriptSha256 = createHash("sha256").update(script).digest("hex");
  const upload = await ssh(`umask 077
run_dir=$(mktemp -d /tmp/dano-deploy-script.XXXXXX) || exit 70
cat > "$run_dir/script.sh" || exit 71
printf '%s\\n' "$run_dir"`, script, 30000);
  if (upload.failed || upload.status !== 0 || !/^\/tmp\/dano-deploy-script\.[a-zA-Z0-9]{6}$/.test(upload.output.trim())) {
    throw new Error("SSH_UPLOAD_FAILED");
  }
  runDirectory = upload.output.trim();
  emit({ stage: "uploaded" });
  // The validated directory and hex digest are the only interpolated values.
  const execution = await ssh(`umask 077
run_dir='${runDirectory}'
test -d "$run_dir" && test ! -L "$run_dir" && test -f "$run_dir/script.sh" && test ! -L "$run_dir/script.sh" || exit 72
digest=$(sha256sum "$run_dir/script.sh") || exit 73
read -r digest ignored <<< "$digest"
test "$digest" = '${scriptSha256}' || exit 74
bash -n "$run_dir/script.sh" > "$run_dir/syntax.log" 2>&1 || exit 75
${kind === "diagnostic" ? "" : `test ! -L /var/lock/dano-production-deploy.lock || exit 76
exec 9>>/var/lock/dano-production-deploy.lock || exit 76
flock -n 9 || exit 76
export DANO_DEPLOY_LOCK_FD=9`}
if bash --noprofile --norc "$run_dir/script.sh" </dev/null > "$run_dir/stdout.log" 2> "$run_dir/stderr.log"; then
  status=0
else
  status=$?
fi
printf '{"exitCode":%d}\\n' "$status" > "$run_dir/step-result.json"
cat "$run_dir/step-result.json"`);
  if (execution.failed || execution.status !== 0) throw new Error("SSH_EXECUTION_UNCONFIRMED");
  let result;
  try { result = JSON.parse(execution.output); } catch { throw new Error("INVALID_EXECUTION_RECEIPT"); }
  if (!result || Object.keys(result).join() !== "exitCode" ||
    !Number.isInteger(result.exitCode) || result.exitCode < 0 || result.exitCode > 255) {
    throw new Error("INVALID_EXECUTION_RECEIPT");
  }
  // A failed command is evidence, not an instruction to mutate production.
  emit({ stage: "completed", exitCode: result.exitCode,
    disposition: result.exitCode === 0 ? "passed" : kind === "diagnostic" ? "retry-diagnostic" : "stop-and-classify" });
} catch (error) {
  const allowed = ["INVALID_ARGUMENTS", "INVALID_SCRIPT", "LOCAL_SYNTAX_FAILED", "SSH_UPLOAD_FAILED",
    "SSH_EXECUTION_UNCONFIRMED", "INVALID_EXECUTION_RECEIPT"];
  emit({ stage: "incomplete", error: allowed.includes(error.message) ? error.message : "REMOTE_SCRIPT_FAILED",
    execution: "unconfirmed", disposition: "inspect-before-mutation" });
  process.exitCode = 2;
}
