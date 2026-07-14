// No-network executable self-test for the recording Pi runtime.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { recordingTools } from "./recording_tools.mjs";

const expectedTools = [
  "get_recording_state",
  "submit_recording_plan",
  "get_validation_report",
  "submit_recording_repair",
  "submit_recording_review",
];

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function verifyPersistentSession(tempDir) {
  const created = SessionManager.create(process.cwd(), tempDir, { id: "recording-persistence-self-test" });
  created.appendMessage({ role: "user", content: [{ type: "text", text: "self-test" }], timestamp: Date.now() });
  // Pi flushes a new JSONL session after its first assistant message.
  created.appendMessage({ role: "assistant", content: [{ type: "text", text: "ok" }], timestamp: Date.now() });
  const opened = SessionManager.open(created.getSessionFile(), tempDir, process.cwd());
  assert(opened.getSessionId() === "recording-persistence-self-test", "SessionManager.open did not restore the session id");
  assert(opened.getEntries().length === 2, "SessionManager.open did not restore session entries");
}

function verifyRuntimeProtocol(tempDir) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["run_recording_pi.mjs"], {
      cwd: path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")),
      env: {
        ...process.env,
        DANO_PI_API_KEY: "self-test-key",
        DANO_PI_BASE_URL: "http://127.0.0.1:9/v1",
        DANO_PI_PROVIDER: "self-test-provider",
        DANO_PI_MODEL: "self-test-model",
        DANO_AGENT_BASE_URL: "http://127.0.0.1:9",
        DANO_AGENT_TOKEN: "self-test-token",
        DANO_AGENT_RUN_ID: "self-test-run",
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let buffer = "";
    let stderr = "";
    let started = false;
    let closed = false;
    let failure;
    const timer = setTimeout(() => {
      failure = new Error("recording runtime self-test timed out");
      child.kill();
    }, 15000);

    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.stdout.on("data", (chunk) => {
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const event = JSON.parse(line);
          if (event.type === "runtime_error") throw new Error(event.error);
          if (event.type === "session_started") {
            assert(event.session_id, "session_started missing session_id");
            assert(event.session_file, "session_started missing session_file");
            assert(event.retry?.enabled, "Pi native retry is not enabled");
            assert(event.compaction?.enabled, "Pi native compaction is not enabled");
            started = true;
            child.stdin.write(`${JSON.stringify({ type: "close", request_id: "close-self-test" })}\n`);
          }
          if (event.type === "session_closed") {
            closed = true;
            child.stdin.end();
          }
        } catch (error) {
          failure = error;
          child.kill();
        }
      }
    });
    child.on("error", reject);
    child.on("exit", (code) => {
      clearTimeout(timer);
      if (failure) return reject(failure);
      if (code !== 0 || !started || !closed) {
        return reject(new Error(`runtime protocol failed (exit=${code}, started=${started}, closed=${closed}): ${stderr}`));
      }
      resolve();
    });

    child.stdin.write(`${JSON.stringify({
      type: "start_session",
      request_id: "start-self-test",
      session_dir: tempDir,
      session_id: "recording-runtime-self-test",
    })}\n`);
  });
}

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-recording-pi-"));
try {
  assert(JSON.stringify(recordingTools.map((tool) => tool.name)) === JSON.stringify(expectedTools), "recording tool allowlist mismatch");
  verifyPersistentSession(tempDir);
  await verifyRuntimeProtocol(tempDir);
  process.stdout.write(`${JSON.stringify({ status: "ok", tools: expectedTools, persistent_session: true, runtime_protocol: true })}\n`);
} finally {
  fs.rmSync(tempDir, { recursive: true, force: true });
}
