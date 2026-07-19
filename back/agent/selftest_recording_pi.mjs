// No-network executable self-test for the recording Pi runtime.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import {
  acceptRecordingToolSubmission,
  beginRecordingToolTurn,
  endRecordingToolTurn,
  guardRecordingToolAttempt,
  recordingTools,
  recordRecordingToolRead,
  runRecordingSubmissionAttempt,
  sanitizeRecordingToolParams,
  requireRecordingSubmissionPrerequisite,
} from "./recording_tools.mjs";

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

function verifySubmissionAttemptLimit() {
  let exceeded = 0;
  beginRecordingToolTurn({ maxSubmissionAttempts: 2, onLimitExceeded: () => { exceeded += 1; } });
  try {
    assert(guardRecordingToolAttempt("get_recording_state") === 0, "read tools must not consume submission budget");
    assert(guardRecordingToolAttempt("submit_recording_plan") === 1, "first submission attempt missing");
    assert(guardRecordingToolAttempt("submit_recording_repair") === 2, "second submission attempt missing");
    let rejected = false;
    try {
      guardRecordingToolAttempt("submit_recording_review");
    } catch (error) {
      rejected = /attempt limit exceeded/.test(String(error?.message || error));
    }
    assert(rejected, "third submission attempt must be rejected");
    assert(exceeded === 1, "submission limit callback must run exactly once");
  } finally {
    endRecordingToolTurn();
  }
}

async function verifySuccessfulSubmissionEndsTurn() {
  const accepted = [];
  beginRecordingToolTurn({ onSubmissionAccepted: (name) => accepted.push(name) });
  try {
    let backendCalls = 0;
    const first = runRecordingSubmissionAttempt("submit_recording_review", async () => {
      backendCalls += 1;
      await Promise.resolve();
      return { ok: true };
    });
    const duplicate = runRecordingSubmissionAttempt("submit_recording_review", async () => {
      backendCalls += 1;
      return { ok: true };
    });
    const [firstResult, duplicateResult] = await Promise.all([first, duplicate]);
    assert(firstResult.duplicate === false, "first successful submission was marked duplicate");
    assert(duplicateResult.duplicate === true, "parallel duplicate submission was not suppressed");
    assert(backendCalls === 1, "parallel duplicate reached the backend");
    assert(guardRecordingToolAttempt("submit_recording_review") === -1, "accepted submission must bypass attempt limit");
    assert(acceptRecordingToolSubmission("submit_recording_review") === false, "duplicate success must not fire twice");
    assert(JSON.stringify(accepted) === JSON.stringify(["submit_recording_review"]), "terminal submission callback mismatch");
  } finally {
    endRecordingToolTurn();
  }
}

async function verifyRejectedThenAcceptedSubmissionIsTerminal() {
  let backendCalls = 0;
  let exceeded = 0;
  beginRecordingToolTurn({
    maxSubmissionAttempts: 2,
    onLimitExceeded: () => { exceeded += 1; },
  });
  try {
    let rejected = false;
    try {
      await runRecordingSubmissionAttempt("submit_recording_review", async () => {
        backendCalls += 1;
        throw new Error("schema rejected");
      });
    } catch (error) {
      rejected = /schema rejected/.test(String(error?.message || error));
    }
    assert(rejected, "first rejected review was not surfaced");
    const accepted = await runRecordingSubmissionAttempt("submit_recording_review", async () => {
      backendCalls += 1;
      return { ok: true };
    });
    const afterAccepted = await runRecordingSubmissionAttempt("submit_recording_review", async () => {
      backendCalls += 1;
      return { ok: true };
    });
    assert(accepted.duplicate === false, "corrected review was not accepted");
    assert(afterAccepted.duplicate === true, "post-success review was not suppressed");
    assert(backendCalls === 2, "post-success review reached the backend");
    assert(exceeded === 0, "post-success review incorrectly triggered the attempt limit");
  } finally {
    endRecordingToolTurn();
  }
}
function verifyFreshReadPrerequisites() {
  beginRecordingToolTurn();
  try {
    let missingReadRejected = false;
    try {
      requireRecordingSubmissionPrerequisite("submit_recording_plan", { base_flow_version: 4 });
    } catch (error) {
      missingReadRejected = /get_recording_state/.test(String(error?.message || error));
    }
    assert(missingReadRejected, "plan submission without a fresh state read must be rejected");
    recordRecordingToolRead("get_recording_state", { flow_version: 4 });
    requireRecordingSubmissionPrerequisite("submit_recording_plan", { base_flow_version: 4 });
    let staleVersionRejected = false;
    try {
      requireRecordingSubmissionPrerequisite("submit_recording_plan", { base_flow_version: 1 });
    } catch (error) {
      staleVersionRejected = /does not match/.test(String(error?.message || error));
    }
    assert(staleVersionRejected, "stale plan base version must be rejected before consuming submission budget");
    assert(guardRecordingToolAttempt("submit_recording_plan") === 1, "fresh-read rejection consumed the submission budget");
  } finally {
    endRecordingToolTurn();
  }
}

function verifyReviewToolSchema() {
  const reviewTool = recordingTools.find((tool) => tool.name === "submit_recording_review");
  assert(reviewTool?.executionMode === "sequential", "terminal review tool must execute sequentially");
  for (const tool of recordingTools.filter((item) => item.name.startsWith("submit_recording_"))) {
    assert(tool.executionMode === "sequential", `${tool.name} must execute sequentially`);
  }
  const reviewSchema = reviewTool?.parameters?.properties?.review;
  assert(reviewSchema?.additionalProperties === false, "review schema must reject unknown top-level fields");
  assert(!("blocking_reasons" in (reviewSchema?.properties || {})), "Pi review schema must use role verdicts for rejection");
  for (const role of ["acceptance", "security", "compliance"]) {
    const roleSchema = reviewSchema?.properties?.[role];
    assert(roleSchema?.additionalProperties === false, `review.${role} must reject unknown fields`);
    assert(
      JSON.stringify(Object.keys(roleSchema?.properties || {}).sort())
        === JSON.stringify(["model_id", "passed", "reasons"]),
      `review.${role} schema fields mismatch`,
    );
  }
}
function verifyPlanToolCompatibility() {
  const planTool = recordingTools.find((tool) => tool.name === "submit_recording_plan");
  assert(planTool?.parameters?.additionalProperties === true, "plan tool must tolerate model explanation fields");
  const plan = { semantic_plan: {}, ops: "" };
  const sanitized = sanitizeRecordingToolParams("submit_recording_plan", {
    recording_id: "rec-self-test",
    flow_version: 3,
    base_flow_version: 3,
    plan,
    description: "model explanation",
    step_id: "flattened-by-model",
  });
  assert(sanitized.plan === plan, "plan payload changed while sanitizing tool params");
  assert(
    JSON.stringify(Object.keys(sanitized).sort())
      === JSON.stringify(["base_flow_version", "flow_version", "plan", "recording_id"]),
    "unknown plan tool params reached the backend",
  );
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
  verifySubmissionAttemptLimit();
  verifyFreshReadPrerequisites();
  await verifySuccessfulSubmissionEndsTurn();
  await verifyRejectedThenAcceptedSubmissionIsTerminal();
  verifyReviewToolSchema();
  verifyPlanToolCompatibility();
  verifyPersistentSession(tempDir);
  await verifyRuntimeProtocol(tempDir);
  process.stdout.write(`${JSON.stringify({ status: "ok", tools: expectedTools, persistent_session: true, runtime_protocol: true })}\n`);
} finally {
  fs.rmSync(tempDir, { recursive: true, force: true });
}
