import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import path from "node:path";
import test from "node:test";

const AGENT_DIR = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));

function startEntry(script, env = {}) {
  return spawn(process.execPath, [script], {
    cwd: AGENT_DIR,
    env: {
      ...process.env,
      DANO_PI_API_KEY: "",
      DANO_PI_BASE_URL: "",
      DANO_PI_PROVIDER: "dano-sdk-compat-test",
      DANO_PI_MODEL: "missing-test-model",
      ...env,
    },
    stdio: ["pipe", "pipe", "pipe"],
  });
}

function collect(stream) {
  let output = "";
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => { output += chunk; });
  return () => output;
}

test("recording Pi entry handles a command with the installed SDK", { timeout: 10000 }, async () => {
  const child = startEntry("run_recording_pi.mjs");
  const stdout = collect(child.stdout);
  const stderr = collect(child.stderr);
  try {
    child.stdin.write(`${JSON.stringify({
      type: "start_session",
      request_id: "sdk-compat-test",
      session_id: "sdk-compat-test",
    })}\n`);
    const outcome = await Promise.race([
      once(child.stdout, "data").then(() => ({ output: stdout() })),
      once(child, "exit").then(([code]) => ({ code })),
    ]);
    assert.ok("output" in outcome, stderr());
    const event = JSON.parse(outcome.output.trim().split(/\r?\n/u)[0]);
    assert.equal(event.type, "runtime_error", outcome.output);
    assert.match(event.error, /no Pi model or credentials/u, outcome.output);
  } finally {
    if (child.exitCode === null) {
      const exited = once(child, "exit");
      child.kill();
      await exited;
    }
  }
});

test("general Pi entry resolves missing credentials without a legacy API failure", { timeout: 10000 }, async () => {
  const child = startEntry("run_pi.mjs");
  const stdout = collect(child.stdout);
  const stderr = collect(child.stderr);
  child.stdin.end(`${JSON.stringify({
    type: "start_run",
    run_id: "sdk-compat-test",
    prompt: "compatibility probe",
    budget: { timeout_s: 5 },
  })}\n`);

  const [code] = await once(child, "exit");
  const events = stdout().trim().split(/\r?\n/u).filter(Boolean).map(JSON.parse);
  const completed = events.find((event) => event.type === "run_completed");

  assert.equal(code, 0, stderr());
  assert.equal(completed?.status, "failed", stdout());
  assert.equal(completed?.error, "no_model_or_credentials", stderr());
});
