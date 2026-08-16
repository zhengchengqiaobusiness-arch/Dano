// No-network executable self-test for the recording Pi runtime.
import { spawn } from "node:child_process";
import fs from "node:fs";
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
  "get_recording_delta",
  "ask_operator",
  "replay_request",
  "perturb_replay",
  "verify_dependency",
  "execute_write_with_verify",
  "browser_navigate",
  "browser_snapshot",
  "browser_click",
  "browser_fill",
  "browser_select",
  "list_link_candidates",
  "get_verification",
  "submit_recording_plan",
  "get_validation_report",
  "submit_recording_repair",
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
      guardRecordingToolAttempt("submit_recording_plan");
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
    const first = runRecordingSubmissionAttempt("submit_recording_repair", async () => {
      backendCalls += 1;
      await Promise.resolve();
      return { ok: true };
    });
    const duplicate = runRecordingSubmissionAttempt("submit_recording_repair", async () => {
      backendCalls += 1;
      return { ok: true };
    });
    const [firstResult, duplicateResult] = await Promise.all([first, duplicate]);
    assert(firstResult.duplicate === false, "first successful submission was marked duplicate");
    assert(duplicateResult.duplicate === true, "parallel duplicate submission was not suppressed");
    assert(backendCalls === 1, "parallel duplicate reached the backend");
    assert(guardRecordingToolAttempt("submit_recording_repair") === -1, "accepted submission must bypass attempt limit");
    assert(acceptRecordingToolSubmission("submit_recording_repair") === false, "duplicate success must not fire twice");
    assert(JSON.stringify(accepted) === JSON.stringify(["submit_recording_repair"]), "terminal submission callback mismatch");
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
      await runRecordingSubmissionAttempt("submit_recording_repair", async () => {
        backendCalls += 1;
        throw new Error("schema rejected");
      });
    } catch (error) {
      rejected = /schema rejected/.test(String(error?.message || error));
    }
    assert(rejected, "first rejected review was not surfaced");
    const accepted = await runRecordingSubmissionAttempt("submit_recording_repair", async () => {
      backendCalls += 1;
      return { ok: true };
    });
    const afterAccepted = await runRecordingSubmissionAttempt("submit_recording_repair", async () => {
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

async function verifyIncompleteSubmissionCanBeCorrectedInTheSameTurn() {
  const accepted = [];
  beginRecordingToolTurn({
    maxSubmissionAttempts: 2,
    onSubmissionAccepted: (name) => accepted.push(name),
  });
  try {
    let backendCalls = 0;
    const incomplete = await runRecordingSubmissionAttempt("submit_recording_plan", async () => {
      backendCalls += 1;
      return { all_applied: false, must_retry: [3] };
    });
    const corrected = await runRecordingSubmissionAttempt("submit_recording_plan", async () => {
      backendCalls += 1;
      return { all_applied: true, must_retry: [] };
    });

    assert(incomplete.duplicate === false, "incomplete plan was marked duplicate");
    assert(incomplete.accepted === false, "incomplete plan incorrectly ended the turn");
    assert(corrected.duplicate === false, "corrected plan was suppressed as a duplicate");
    assert(corrected.accepted === true, "corrected plan did not end the turn");
    assert(backendCalls === 2, "corrected plan did not reach the backend");
    assert(JSON.stringify(accepted) === JSON.stringify(["submit_recording_plan"]), "only the complete plan may be terminal");
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

function verifySubmissionToolsAreSequential() {
  for (const tool of recordingTools.filter((item) => item.name.startsWith("submit_recording_"))) {
    assert(tool.executionMode === "sequential", `${tool.name} must execute sequentially`);
  }
}

function verifyWriteAssertionSchema() {
  const tool = recordingTools.find((item) => item.name === "execute_write_with_verify");
  assert(
    !(tool?.parameters?.required || []).includes("inputs"),
    "write verification must allow replaying the captured body without redundant inputs",
  );
  const assertion = tool?.parameters?.properties?.assertion;
  assert(assertion?.anyOf?.length === 4, "write assertion schema must expose three executable contracts and string compatibility");
  assert(
    assertion.anyOf.slice(0, 3).every((schema) => schema.additionalProperties === false),
    "write assertion variants must reject unknown keys",
  );
  const countAssertion = assertion.anyOf.find((schema) => (
    schema?.properties?.verify_records_min_count
  ));
  assert(
    countAssertion?.properties?.verify_records_min_count?.type === "integer",
    "write assertion schema must expose verify_records_min_count",
  );
  const collectionAssertion = assertion.anyOf.find((schema) => schema?.properties?.collection_path);
  assert(
    JSON.stringify(collectionAssertion?.required?.sort())
      === JSON.stringify(["collection_path", "min_matches", "where"].sort()),
    "collection assertion must require collection_path, where and min_matches together",
  );
}

function verifyStringifiedWriteAssertionCompatibility() {
  const sanitized = sanitizeRecordingToolParams("execute_write_with_verify", {
    write_step_id: "submit",
    inputs: { title: "recorded" },
    verify_request_id: "req-read",
    assertion: JSON.stringify({ verify_records_min_count: 1 }),
  });
  assert(
    sanitized.assertion?.verify_records_min_count === 1,
    "one JSON-stringified write assertion was not decoded at the tool boundary",
  );
}

function verifyPerturbReplaySchema() {
  const tool = recordingTools.find((item) => item.name === "perturb_replay");
  const perturb = tool?.parameters?.properties?.perturb;
  assert(perturb?.additionalProperties === false, "perturb replay must reject request-id keyed overrides");
  assert(
    JSON.stringify(Object.keys(perturb?.properties || {}).sort())
      === JSON.stringify(["body", "headers", "query", "url_path"]),
    "perturb replay override fields mismatch",
  );
}
function verifyDependencySchema() {
  const tool = recordingTools.find((item) => item.name === "verify_dependency");
  assert(tool?.parameters?.additionalProperties === false, "dependency verification must reject unknown keys");
  assert(
    JSON.stringify(Object.keys(tool?.parameters?.properties || {}).sort())
      === JSON.stringify(["link_id"]),
    "dependency verification must accept only executor-owned link_id",
  );
}
function verifyDeltaPaginationSchema() {
  const tool = recordingTools.find((item) => item.name === "get_recording_delta");
  const limit = tool?.parameters?.properties?.limit;
  assert(limit?.type === "integer" && limit.maximum === 50, "delta tool must expose a bounded page limit");
  assert(tool?.description?.includes("has_more"), "delta tool must explain cursor pagination to Pi");
}
function verifyServerOwnedRecordingContext() {
  for (const tool of recordingTools) {
    const properties = tool?.parameters?.properties || {};
    assert(!("recording_id" in properties), `${tool.name} must not expose server-owned recording_id`);
    assert(!("flow_version" in properties), `${tool.name} must not expose server-owned flow_version`);
  }
  assert(
    JSON.stringify(sanitizeRecordingToolParams("get_recording_state", {
      recording_id: "recording-from-old-session",
      flow_version: 999,
    })) === JSON.stringify({}),
    "model-supplied recording identity reached the backend bridge",
  );
}
function verifyPlanToolCompatibility() {
  const planTool = recordingTools.find((tool) => tool.name === "submit_recording_plan");
  const planSchema = planTool?.parameters?.properties?.plan;
  const planVariants = planSchema?.anyOf || [];
  const structuredPlanSchema = planVariants.find((variant) => variant?.type === "object");
  assert(
    structuredPlanSchema && planVariants.some((variant) => variant?.type === "string"),
    "plan boundary must accept structured plans and recover JSON-stringified plans",
  );
  assert(
    planTool?.description?.includes("plan.ops")
      && planTool.description.includes("set_param_source")
      && planTool.description.includes("op_results")
      && planTool.description.includes("propose_dependency")
      && planTool.description.includes("must_retry"),
    "plan tool does not expose the live semantic operation channel to Pi",
  );
  assert(
    structuredPlanSchema?.properties?.ops?.type === "array",
    "plan tool schema does not declare the live semantic operation channel",
  );
  const liveOps = structuredPlanSchema?.properties?.ops?.items?.anyOf || [];
  const expandedOps = liveOps.flatMap((item) => item?.anyOf || [item]);
  const operationSchema = (name) => expandedOps.find((item) => item?.properties?.op?.const === name);
  const operationSchemas = (name) => expandedOps.filter((item) => item?.properties?.op?.const === name);
  assert(
    operationSchema("set_request_role")?.required?.includes("evidence_refs"),
    "set_request_role schema must expose evidence_refs",
  );
  assert(
    operationSchema("set_param_source")?.properties?.source_kind?.anyOf?.length === 7,
    "set_param_source schema must expose the seven executable source categories",
  );
  assert(
    operationSchema("set_param_source")?.properties?.evidence_refs?.type === "array",
    "set_param_source schema must accept grounded evidence_refs",
  );
  assert(
    operationSchema("set_param_required")?.required?.includes("evidence_refs"),
    "set_param_required schema must require evidence_refs",
  );
  assert(
    operationSchema("rename_field")?.required?.includes("evidence_refs"),
    "rename_field schema must require evidence_refs",
  );
  assert(
    operationSchema("set_param_enum")?.required?.includes("evidence_refs"),
    "set_param_enum schema must require evidence_refs",
  );
  assert(
    operationSchemas("propose_dependency").length === 2
      && operationSchemas("propose_dependency").every((item) => item?.required?.includes("evidence")),
    "propose_dependency schema must require evidence",
  );
  const responseKeyMap = operationSchemas("propose_dependency").find(
    (item) => item?.properties?.kind?.const === "response_key_map",
  );
  assert(
    responseKeyMap?.required?.includes("source_collection_path")
      && responseKeyMap?.required?.includes("source_key_path")
      && responseKeyMap?.required?.includes("source_label_path")
      && responseKeyMap?.required?.includes("target_container_path")
      && responseKeyMap?.required?.includes("value_binding"),
    "response_key_map schema must require the complete dynamic structure contract",
  );
  assert(
    operationSchema("bind_verify_read")?.required?.includes("read_request_id")
      && operationSchema("bind_verify_read")?.required?.includes("assertion")
      && operationSchema("bind_verify_read")?.additionalProperties === false,
    "bind_verify_read must be rejected before execution when its subject is incomplete",
  );
  assert(
    !planTool?.parameters?.required?.includes("recording_id"),
    "recording_id belongs to the active server session and must not block a model submission",
  );
  assert(
    !planTool?.parameters?.required?.includes("plan"),
    "a length-truncated plan must reach the deterministic fallback instead of retrying forever",
  );
  assert(planTool?.parameters?.additionalProperties === true, "plan tool must tolerate model explanation fields");
  assert(
    structuredPlanSchema?.additionalProperties === false,
    "plan payload must reject undeclared planner aliases",
  );
  const semanticSchema = structuredPlanSchema?.properties?.semantic_plan;
  assert(
    semanticSchema?.additionalProperties === false
      && JSON.stringify(Object.keys(semanticSchema?.properties || {}).sort())
        === JSON.stringify(["business_understanding", "capabilities", "unresolved_items"]),
    "semantic plan must expose only the strict business/capability/unresolved contract",
  );
  const capabilitySchema = semanticSchema?.properties?.capabilities?.items;
  assert(
    capabilitySchema?.required?.includes("anchor_step_id")
      && capabilitySchema?.additionalProperties === false
      && capabilitySchema?.properties?.request_refs?.items?.properties?.usage?.anyOf?.length === 4,
    "capabilities must require an anchor and typed request usage",
  );
  const repairTool = recordingTools.find((tool) => tool.name === "submit_recording_repair");
  assert(
    JSON.stringify(repairTool?.parameters?.properties?.operations?.items)
      === JSON.stringify(structuredPlanSchema?.properties?.ops?.items),
    "plan and repair must use the same typed operation union",
  );
  const plan = {
    semantic_plan: {
      business_understanding: { intent: "Create request" },
      capabilities: [{
        name: "submit_request",
        title: "Submit request",
        kind: "submit",
        anchor_step_id: "submit",
        request_refs: [{ step_id: "submit", usage: "execute" }],
      }],
      unresolved_items: [],
    },
    ops: [{
      op: "set_param_source",
      request_id: "req-submit",
      wire_path: "body.title",
      source_kind: "caller_input",
      reason: "operator entered this field",
      evidence_refs: ["event-title"],
    }],
  };
  const sanitized = sanitizeRecordingToolParams("submit_recording_plan", {
    recording_id: "rec-self-test",
    flow_version: 3,
    base_flow_version: 3,
    plan,
    description: "model explanation",
    step_id: "flattened-by-model",
  });
  assert(sanitized.plan !== plan, "plan payload was not canonicalized");
  assert(
    JSON.stringify(Object.keys(sanitized).sort())
      === JSON.stringify(["base_flow_version", "plan"]),
    "unknown plan tool params reached the backend",
  );
  const semantic = sanitized.plan.semantic_plan;
  assert(
    sanitized.plan._submitted_semantic_keys.includes("capabilities"),
    "originally submitted semantic keys were not preserved",
  );
  assert(semantic.capabilities.length === 1, "strict capability was discarded");
  assert(Array.isArray(semantic.unresolved_items), "unresolved_items were not restored");
  assert(sanitized.plan.ops.length === 1, "typed operation was discarded");
  assert(!("field_semantics" in semantic), "field semantics bypassed plan.ops");

  const drifted = sanitizeRecordingToolParams("submit_recording_plan", {
    base_flow_version: 3,
    plan: {
      semantic_plan: {
        business_understanding: { summary: "Submit", risk_level: "low" },
        capabilities: [{
          name: "submit_request", title: "Submit", kind: "submit",
          anchor_step_id: "req-submit",
        }],
      },
      ops: [{
        op: "set_request_role", request_id: "req-submit",
        role: "business_write", reason: "form submit",
        evidence: ["req-submit"],
      }],
    },
  });
  assert(
    !("risk_level" in drifted.plan.semantic_plan.business_understanding),
    "descriptive risk_level was not removed before strict validation",
  );
  assert(
    drifted.plan.semantic_plan.capabilities[0].request_refs[0].usage === "execute",
    "missing request_refs were not derived from the explicit anchor",
  );
  assert(
    drifted.plan.ops[0].evidence_refs[0] === "req-submit"
      && !("evidence" in drifted.plan.ops[0]),
    "evidence alias was not canonicalized",
  );

  const stringified = sanitizeRecordingToolParams("submit_recording_plan", {
    base_flow_version: 3,
    plan: JSON.stringify(plan),
  });
  assert(
    JSON.stringify(stringified.plan) === JSON.stringify(sanitized.plan),
    "JSON-stringified plans were not recovered to the canonical structured plan",
  );
}

function verifyTruncatedPlanFallback() {
  const sanitized = sanitizeRecordingToolParams("submit_recording_plan", {
    base_flow_version: 2,
    flow_version: 2,
  });
  assert(
    sanitized.submission_error === "model_output_truncated_missing_plan",
    "missing plan was not converted into a terminal backend fallback",
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

const agentDir = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const testTempDir = path.join(path.dirname(path.dirname(agentDir)), ".runtime", "node-tests");
fs.mkdirSync(testTempDir, { recursive: true });
const tempDir = fs.mkdtempSync(path.join(testTempDir, "dano-recording-pi-"));
try {
  assert(JSON.stringify(recordingTools.map((tool) => tool.name)) === JSON.stringify(expectedTools), "recording tool allowlist mismatch");
  verifySubmissionAttemptLimit();
  verifyFreshReadPrerequisites();
  await verifySuccessfulSubmissionEndsTurn();
  await verifyRejectedThenAcceptedSubmissionIsTerminal();
  await verifyIncompleteSubmissionCanBeCorrectedInTheSameTurn();
  verifySubmissionToolsAreSequential();
verifyWriteAssertionSchema();
  verifyStringifiedWriteAssertionCompatibility();
verifyPerturbReplaySchema();
verifyDependencySchema();
verifyDeltaPaginationSchema();
  verifyServerOwnedRecordingContext();
  verifyPlanToolCompatibility();
  verifyTruncatedPlanFallback();
  verifyPersistentSession(tempDir);
  await verifyRuntimeProtocol(tempDir);
  process.stdout.write(`${JSON.stringify({ status: "ok", tools: expectedTools, persistent_session: true, runtime_protocol: true })}\n`);
} finally {
  fs.rmSync(tempDir, { recursive: true, force: true });
}
