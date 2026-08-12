// Recording-only Pi tools. Every tool is a thin authenticated proxy to Dano.
// The authoritative recording state and all mutations remain in Python.
import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const BASE_URL = process.env.DANO_AGENT_BASE_URL;
const TOKEN = process.env.DANO_AGENT_TOKEN;
const RUN_ID = process.env.DANO_AGENT_RUN_ID;
const SUBMISSION_TOOLS = new Set([
  "submit_recording_plan",
  "submit_recording_repair",
  "submit_recording_review",
]);
let activeTurnBudget = null;

export function beginRecordingToolTurn({
  maxSubmissionAttempts = 2,
  onLimitExceeded,
  onSubmissionAccepted,
} = {}) {
  activeTurnBudget = {
    attempts: 0,
    maxSubmissionAttempts: Math.max(1, Number.parseInt(String(maxSubmissionAttempts), 10) || 2),
    onLimitExceeded,
    onSubmissionAccepted,
    acceptedSubmission: "",
    limitReported: false,
    freshStateVersion: null,
    freshValidationVersion: null,
    submissionTail: Promise.resolve(),
  };
}

export function endRecordingToolTurn() {
  activeTurnBudget = null;
}

export function guardRecordingToolAttempt(name, turn = activeTurnBudget) {
  if (!turn || !SUBMISSION_TOOLS.has(name)) return 0;
  // Once one terminal submission has been persisted, later tool calls from the
  // same model turn are harmless duplicates, not failed attempts.
  if (turn.acceptedSubmission) return -1;
  turn.attempts += 1;
  if (turn.attempts <= turn.maxSubmissionAttempts) {
    return turn.attempts;
  }
  const error = new Error(
    `recording submission attempt limit exceeded (${turn.maxSubmissionAttempts}); `
    + "stop this turn and read fresh state before a new request",
  );
  if (!turn.limitReported) {
    turn.limitReported = true;
    turn.onLimitExceeded?.(error);
  }
  throw error;
}

export function acceptRecordingToolSubmission(name, turn = activeTurnBudget) {
  if (!turn || !SUBMISSION_TOOLS.has(name)) return false;
  if (turn.acceptedSubmission) return false;
  turn.acceptedSubmission = name;
  turn.onSubmissionAccepted?.(name);
  return true;
}

export function recordRecordingToolRead(name, output, turn = activeTurnBudget) {
  if (!turn || !output || typeof output !== "object") return;
  const version = Number(output.flow_version);
  if (!Number.isInteger(version) || version < 0) return;
  if (name === "get_recording_state") turn.freshStateVersion = version;
  if (name === "get_validation_report") turn.freshValidationVersion = version;
}

export function requireRecordingSubmissionPrerequisite(name, params, turn = activeTurnBudget) {
  if (!turn || !SUBMISSION_TOOLS.has(name)) return;
  const baseVersion = Number(params?.base_flow_version);
  const requireVersion = (label, version) => {
    if (!Number.isInteger(version)) {
      throw new Error(`${name} requires ${label} in the current turn before submission`);
    }
    if (!Number.isInteger(baseVersion) || baseVersion !== version) {
      throw new Error(
        `${name} base_flow_version=${String(params?.base_flow_version)} does not match `
        + `fresh ${label} flow_version=${version}`,
      );
    }
  };
  if (name === "submit_recording_plan") {
    requireVersion("get_recording_state", turn.freshStateVersion);
  } else if (name === "submit_recording_repair") {
    requireVersion("get_validation_report", turn.freshValidationVersion);
  } else if (name === "submit_recording_review") {
    requireVersion("get_recording_state", turn.freshStateVersion);
    requireVersion("get_validation_report", turn.freshValidationVersion);
    if (turn.freshStateVersion !== turn.freshValidationVersion) {
      throw new Error("submit_recording_review requires state and validation from the same flow version");
    }
  }
}
export async function runRecordingSubmissionAttempt(name, operation) {
  const turn = activeTurnBudget;
  if (!turn || !SUBMISSION_TOOLS.has(name)) {
    return { output: await operation(), duplicate: false };
  }

  // Pi may execute tool calls from one assistant message concurrently. Queue
  // terminal submissions so only one can reach Python at a time; after one is
  // accepted, queued duplicates return success without another HTTP mutation.
  const previous = turn.submissionTail;
  let release;
  turn.submissionTail = new Promise((resolve) => { release = resolve; });
  await previous;
  try {
    if (turn.acceptedSubmission) {
      return {
        output: {
          ok: true,
          status: "already_submitted",
          accepted_submission: turn.acceptedSubmission,
        },
        duplicate: true,
      };
    }
    guardRecordingToolAttempt(name, turn);
    const output = await operation();
    acceptRecordingToolSubmission(name, turn);
    return { output, duplicate: false };
  } finally {
    release();
  }
}

function requireBridgeEnvironment() {
  const missing = [];
  if (!BASE_URL) missing.push("DANO_AGENT_BASE_URL");
  if (!TOKEN) missing.push("DANO_AGENT_TOKEN");
  if (!RUN_ID) missing.push("DANO_AGENT_RUN_ID");
  if (missing.length) throw new Error(`missing recording tool environment: ${missing.join(", ")}`);
}

export async function callRecordingTool(name, params, toolCallId) {
  requireBridgeEnvironment();
  const response = await fetch(`${BASE_URL}/_agent/tools/${name}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Agent-Token": TOKEN,
    },
    body: JSON.stringify({
      run_id: RUN_ID,
      tool_call_id: toolCallId,
      params,
    }),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`recording tool ${name} HTTP ${response.status}: ${text}`);
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`recording tool ${name} returned non-JSON response`);
  }
}

function proxyTool({ name, label, description, parameters }) {
  return defineTool({
    name,
    label,
    description,
    parameters,
    ...(SUBMISSION_TOOLS.has(name) ? { executionMode: "sequential" } : {}),
    execute: async (toolCallId, params) => {
      const sanitizedParams = sanitizeRecordingToolParams(name, params);
      if (SUBMISSION_TOOLS.has(name)) {
        requireRecordingSubmissionPrerequisite(name, sanitizedParams);
        const { output } = await runRecordingSubmissionAttempt(
          name,
          () => callRecordingTool(name, sanitizedParams, toolCallId),
        );
        return {
          content: [{ type: "text", text: JSON.stringify(output) }],
          isError: false,
          // This is the SDK-native terminal signal. The abort callback in the
          // runtime remains a fallback for mixed parallel tool batches.
          terminate: true,
        };
      }
      const output = await callRecordingTool(name, sanitizedParams, toolCallId);
      recordRecordingToolRead(name, output);
      return {
        content: [{ type: "text", text: JSON.stringify(output) }],
        isError: false,
      };
    },
  });
}

const SEMANTIC_PLAN_KEYS = [
  "business_understanding",
  "capabilities",
  "unresolved_items",
];

function asSemanticArray(value) {
  const isRecord = (item) => (
    (item && typeof item === "object" && !Array.isArray(item))
    || (typeof item === "string" && item.trim())
  );
  if (Array.isArray(value)) return value.filter(isRecord);
  return isRecord(value) ? [value] : [];
}

function normalizedConfidence(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.max(0, Math.min(1, value));
  }
  const normalized = String(value ?? "").trim().toLowerCase();
  if (normalized === "high") return 0.95;
  if (normalized === "medium") return 0.75;
  if (normalized === "low") return 0.4;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? Math.max(0, Math.min(1, parsed)) : value;
}

function normalizeConfidenceDeep(value) {
  if (Array.isArray(value)) return value.map(normalizeConfidenceDeep);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [
    key,
    key === "confidence" ? normalizedConfidence(item) : normalizeConfidenceDeep(item),
  ]));
}

export function canonicalizeRecordingPlan(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("submit_recording_plan.plan must be an object");
  }
  const rawSemantic = (
    value.semantic_plan
    && typeof value.semantic_plan === "object"
    && !Array.isArray(value.semantic_plan)
  ) ? value.semantic_plan : {};
  const semantic = { ...rawSemantic };
  const submittedSemanticKeys = SEMANTIC_PLAN_KEYS.filter(
    (key) => semantic[key] !== undefined || value[key] !== undefined,
  );
  for (const key of SEMANTIC_PLAN_KEYS) {
    if (semantic[key] === undefined && value[key] !== undefined) {
      semantic[key] = value[key];
    }
  }
  semantic.business_understanding = (
    semantic.business_understanding
    && typeof semantic.business_understanding === "object"
    && !Array.isArray(semantic.business_understanding)
  ) ? semantic.business_understanding : {};
  semantic.capabilities = asSemanticArray(semantic.capabilities);
  semantic.unresolved_items = asSemanticArray(semantic.unresolved_items);
  return normalizeConfidenceDeep({
    _submitted_semantic_keys: submittedSemanticKeys,
    semantic_plan: semantic,
    ops: Array.isArray(value.ops) ? value.ops : [],
  });
}

export function sanitizeRecordingToolParams(name, params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return params;
  params = Object.fromEntries(
    Object.entries(params).filter(([key]) => !["recording_id", "flow_version"].includes(key)),
  );
  if (name !== "submit_recording_plan") return params;
  const allowed = ["base_flow_version", "plan"];
  const sanitized = Object.fromEntries(
    allowed.filter((key) => key in params).map((key) => [key, params[key]]),
  );
  let plan = (
    sanitized.plan
    && typeof sanitized.plan === "object"
    && !Array.isArray(sanitized.plan)
  ) ? { ...sanitized.plan } : sanitized.plan;
  // Some OpenAI-compatible models serialize a nested object argument as one
  // JSON string even though the tool schema says object.  Decode exactly one
  // object layer here; the canonicalizer and Python fact/version gates remain
  // authoritative for its contents.
  if (typeof plan === "string" && plan.trim()) {
    try {
      const decoded = JSON.parse(plan);
      if (decoded && typeof decoded === "object" && !Array.isArray(decoded)) {
        plan = decoded;
      }
    } catch {
      // Keep the original value so the existing missing-plan path reports a
      // deterministic rejected submission rather than accepting malformed JSON.
    }
  }
  if (!plan || typeof plan !== "object" || Array.isArray(plan)) {
    // A completed tool-call stream can be cut at the model output limit after
    // the small version fields but before the large plan object.  Send that
    // deterministic condition to Python so the turn terminates with a visible
    // unchanged result instead of letting the SDK retry indefinitely.
    delete sanitized.plan;
    sanitized.submission_error = "model_output_truncated_missing_plan";
    return sanitized;
  }
  if (plan && typeof plan === "object" && !Array.isArray(plan)) {
    const outerSemantic = (
      params.semantic_plan
      && typeof params.semantic_plan === "object"
      && !Array.isArray(params.semantic_plan)
    ) ? params.semantic_plan : {};
    const semantic = (
      plan.semantic_plan
      && typeof plan.semantic_plan === "object"
      && !Array.isArray(plan.semantic_plan)
    ) ? { ...plan.semantic_plan } : {};
    const mergeObjectArrays = (...values) => {
      const seen = new Set();
      return values.flatMap(asSemanticArray).filter((item) => {
        const signature = JSON.stringify(item);
        if (seen.has(signature)) return false;
        seen.add(signature);
        return true;
      });
    };
    for (const key of SEMANTIC_PLAN_KEYS) {
      if (key === "business_understanding") {
        const meaningful = [semantic[key], outerSemantic[key], params[key]].find((value) => (
          (typeof value === "string" && value.trim())
          || (
            value && typeof value === "object" && !Array.isArray(value)
            && Object.keys(value).length > 0
          )
        ));
        if (meaningful !== undefined) semantic[key] = meaningful;
        continue;
      }
      const merged = mergeObjectArrays(semantic[key], outerSemantic[key], params[key]);
      if (merged.length || semantic[key] !== undefined || outerSemantic[key] !== undefined || params[key] !== undefined) {
        semantic[key] = merged;
      }
    }
    plan.semantic_plan = semantic;
    const operationValues = [plan.ops]
      .flatMap((value) => Array.isArray(value) ? value : [value])
      .filter((item) => item && typeof item === "object" && !Array.isArray(item));
    const operationSignatures = new Set();
    plan.ops = operationValues.filter((item) => {
      const signature = JSON.stringify(item);
      if (operationSignatures.has(signature)) return false;
      operationSignatures.add(signature);
      return true;
    });
  }
  sanitized.plan = canonicalizeRecordingPlan(plan);
  return sanitized;
}


const RecordingIdentity = {
  // The authenticated bridge owns recording_id and flow_version. Keeping both
  // out of model-visible schemas prevents stale session history from guessing
  // them or asking the operator for internal runtime state.
};

// The SDK validates tool arguments before execute/sanitization. Keep this
// boundary object-shaped but tolerant, then canonicalize deterministically and
// let the backend enforce the complete semantic/fact contract.
const GoalEvidence = Type.Object({
  source: Type.String({ minLength: 1 }),
  ref: Type.Optional(Type.String({ minLength: 1 })),
  detail: Type.Optional(Type.String({ minLength: 1 })),
}, { additionalProperties: true });

const RecordingBindingAssertion = Type.Object({
  path: Type.Optional(Type.String()),
  response_path: Type.Optional(Type.String()),
  operator: Type.Optional(Type.Union([
    Type.Literal("equals"), Type.Literal("eq"), Type.Literal("not_equals"),
    Type.Literal("ne"), Type.Literal("contains"), Type.Literal("exists"),
    Type.Literal("truthy"),
  ])),
  equals: Type.Optional(Type.Any()),
  value: Type.Optional(Type.Any()),
  equals_input: Type.Optional(Type.String()),
  input_path: Type.Optional(Type.String()),
  verify_records_min_count: Type.Optional(Type.Integer({ minimum: 0 })),
  collection_path: Type.Optional(Type.String({ minLength: 1 })),
  where: Type.Optional(Type.Record(Type.String(), Type.Union([
    Type.Object({ equals_input: Type.String({ minLength: 1 }) }, { additionalProperties: false }),
    Type.Object({ equals: Type.Any() }, { additionalProperties: false }),
  ]))),
  min_matches: Type.Optional(Type.Integer({ minimum: 1 })),
}, { additionalProperties: false, minProperties: 1 });

const LiveRecordingOperation = Type.Union([
  Type.Object({
    op: Type.Literal("set_goal"),
    goal: Type.Object({
      intent: Type.String({ minLength: 1 }),
      required_inputs: Type.Optional(Type.Array(Type.String())),
      success_criteria: Type.Optional(Type.Array(Type.String())),
      output_expectation: Type.Optional(Type.Array(Type.String())),
      forbidden_actions: Type.Optional(Type.Array(Type.String())),
      risk_level: Type.Optional(Type.String()),
      capabilities: Type.Optional(Type.Array(Type.String())),
      evidence: Type.Array(GoalEvidence, { minItems: 1 }),
    }, { additionalProperties: false }),
  }, { additionalProperties: false }),
  Type.Object({
    op: Type.Literal("set_request_role"),
    request_id: Type.String({ minLength: 1 }),
    role: Type.String({ minLength: 1 }),
    reason: Type.String({ minLength: 1 }),
    evidence_refs: Type.Array(Type.String({ minLength: 1 }), { minItems: 1 }),
    confidence: Type.Optional(Type.Number({ minimum: 0, maximum: 1 })),
  }, { additionalProperties: false }),
  Type.Object({
    op: Type.Literal("set_param_source"),
    step_id: Type.Optional(Type.String({ minLength: 1 })),
    request_id: Type.Optional(Type.String({ minLength: 1 })),
    wire_path: Type.String({ minLength: 1 }),
    source_kind: Type.Union([
      Type.Literal("user_input"), Type.Literal("constant"),
      Type.Literal("session_header"), Type.Literal("page_context"),
      Type.Literal("chained"), Type.Literal("computed"),
    ]),
    origin_request_id: Type.Optional(Type.String({ description: "Required for chained: the upstream request that produced the value" })),
    origin_path: Type.Optional(Type.String({ description: "Required for chained: response path of the upstream value" })),
    context_key: Type.Optional(Type.String({ description: "Optional for page_context; defaults to the last path segment" })),
    strategy: Type.Optional(Type.String({ description: "Required for computed; only date_span_days_json is executable" })),
    start_field: Type.Optional(Type.String({ description: "Required for computed: user param name for the range start" })),
    end_field: Type.Optional(Type.String({ description: "Required for computed: user param name for the range end" })),
    output_key: Type.Optional(Type.String({ description: "Computed JSON key; when omitted it is inferred from the recorded one-key JSON sample" })),
    reason: Type.String({ minLength: 1 }),
  }, { additionalProperties: false }),
  Type.Object({
    op: Type.Literal("set_param_required"),
    step_id: Type.Optional(Type.String({ minLength: 1 })),
    request_id: Type.Optional(Type.String({ minLength: 1 })),
    wire_path: Type.String({ minLength: 1 }),
    required: Type.Boolean(),
    reason: Type.String({ minLength: 1 }),
    evidence_refs: Type.Array(Type.String({ minLength: 1 }), { minItems: 1 }),
  }, { additionalProperties: false }),
  Type.Object({
    op: Type.Literal("set_param_enum"),
    step_id: Type.Optional(Type.String({ minLength: 1 })),
    request_id: Type.Optional(Type.String({ minLength: 1 })),
    wire_path: Type.String({ minLength: 1 }),
    dictionary_source: Type.Optional(Type.String({ minLength: 1 })),
    options: Type.Array(Type.Object({
      label: Type.String({ minLength: 1 }),
      value: Type.Unknown(),
    }, { additionalProperties: false }), { minItems: 1 }),
    reason: Type.String({ minLength: 1 }),
    evidence_refs: Type.Array(Type.String({ minLength: 1 }), { minItems: 1 }),
  }, { additionalProperties: false }),
  Type.Object({
    op: Type.Literal("rename_field"),
    step_id: Type.Optional(Type.String({ minLength: 1 })),
    request_id: Type.Optional(Type.String({ minLength: 1 })),
    wire_path: Type.String({ minLength: 1 }),
    label: Type.String({ minLength: 1 }),
    reason: Type.String({ minLength: 1 }),
    evidence_refs: Type.Array(Type.String({ minLength: 1 }), { minItems: 1 }),
  }, { additionalProperties: false }),
  Type.Union([
    Type.Object({
      op: Type.Literal("propose_dependency"),
      link_id: Type.Optional(Type.String({ minLength: 1 })),
      kind: Type.Optional(Type.Union([Type.Literal("value"), Type.Literal("structure")])),
      source_request_id: Type.String({ minLength: 1 }),
      source_path: Type.String({ minLength: 1 }),
      target_request_id: Type.Optional(Type.String({ minLength: 1 })),
      target_step_id: Type.Optional(Type.String({ minLength: 1 })),
      target_path: Type.String({ minLength: 1 }),
      reason: Type.Optional(Type.String({ minLength: 1 })),
      confidence: Type.Optional(Type.Number({ minimum: 0, maximum: 1 })),
      evidence: Type.Object({}, { additionalProperties: true, minProperties: 1 }),
    }, { additionalProperties: false }),
    Type.Object({
      op: Type.Literal("propose_dependency"),
      link_id: Type.Optional(Type.String({ minLength: 1 })),
      kind: Type.Literal("response_key_map"),
      source_request_id: Type.String({ minLength: 1 }),
      source_collection_path: Type.String({ minLength: 1 }),
      source_key_path: Type.String({ minLength: 1 }),
      source_label_path: Type.String({ minLength: 1 }),
      target_request_id: Type.Optional(Type.String({ minLength: 1 })),
      target_step_id: Type.Optional(Type.String({ minLength: 1 })),
      target_container_path: Type.String({ minLength: 1 }),
      value_binding: Type.Object({
        kind: Type.Literal("caller_map_by_label"),
        input_field: Type.String({ minLength: 1 }),
        option_source: Type.Optional(Type.Union([
          Type.Object({
            capability: Type.String({ minLength: 1 }),
            value_path: Type.String({ minLength: 1 }),
            label_path: Type.String({ minLength: 1 }),
          }, { additionalProperties: false }),
          Type.Object({
            source_request_id: Type.String({ minLength: 1 }),
            value_path: Type.String({ minLength: 1 }),
            label_path: Type.String({ minLength: 1 }),
          }, { additionalProperties: false }),
        ])),
      }, { additionalProperties: false }),
      reason: Type.Optional(Type.String({ minLength: 1 })),
      confidence: Type.Optional(Type.Number({ minimum: 0, maximum: 1 })),
      evidence: Type.Object({}, { additionalProperties: true, minProperties: 1 }),
    }, { additionalProperties: false }),
  ]),
  Type.Object({
    op: Type.Literal("add_pitfall"),
    text: Type.String({ minLength: 1 }),
    evidence_ref: Type.Optional(Type.String()),
  }, { additionalProperties: false }),
  Type.Object({
    op: Type.Literal("confirm_dependency"),
    link_id: Type.String({ minLength: 1 }),
    verification_id: Type.String({ minLength: 1 }),
  }, { additionalProperties: false }),
  Type.Object({
    op: Type.Literal("bind_verify_read"),
    write_step_id: Type.String({ minLength: 1 }),
    read_request_id: Type.String({ minLength: 1 }),
    verification_id: Type.String({ minLength: 1 }),
    assertion: RecordingBindingAssertion,
  }, { additionalProperties: false }),
  Type.Object({
    op: Type.Literal("attach_enum_options"),
    step_id: Type.Optional(Type.String({ minLength: 1 })),
    request_id: Type.Optional(Type.String({ minLength: 1 })),
    wire_path: Type.String({ minLength: 1 }),
    source_request_id: Type.String({ minLength: 1 }),
    verification_id: Type.String({ minLength: 1 }),
    options: Type.Array(Type.Any(), { minItems: 1 }),
  }, { additionalProperties: false }),
  Type.Object({
    op: Type.Literal("mark_unverified"),
    target_kind: Type.Union([
      Type.Literal("dependency"), Type.Literal("write_verify"), Type.Literal("enum"),
    ]),
    target_id: Type.String({ minLength: 1 }),
    reason: Type.String({ minLength: 1 }),
  }, { additionalProperties: false }),
]);

const CapabilityKind = Type.Union([
  Type.Literal("query"), Type.Literal("query_status"), Type.Literal("list_options"),
  Type.Literal("validate"), Type.Literal("validate_batch"), Type.Literal("preview"),
  Type.Literal("inspect"), Type.Literal("export"), Type.Literal("create"),
  Type.Literal("update"), Type.Literal("save_draft"), Type.Literal("submit"),
  Type.Literal("submit_batch"), Type.Literal("approve"), Type.Literal("reject"),
  Type.Literal("withdraw"), Type.Literal("delete"),
]);

const SemanticPlan = Type.Object({
  business_understanding: Type.Optional(Type.Object({
    business_name: Type.Optional(Type.String()),
    summary: Type.Optional(Type.String()),
    intent: Type.Optional(Type.String()),
    object: Type.Optional(Type.String()),
    purpose: Type.Optional(Type.String()),
  }, { additionalProperties: false })),
  capabilities: Type.Optional(Type.Array(Type.Object({
    name: Type.String({ minLength: 1 }),
    title: Type.String({ minLength: 1 }),
    kind: CapabilityKind,
    anchor_step_id: Type.String({ minLength: 1 }),
    request_refs: Type.Array(Type.Object({
      step_id: Type.String({ minLength: 1 }),
      usage: Type.Union([
        Type.Literal("execute"), Type.Literal("preflight"),
        Type.Literal("option_source"), Type.Literal("fact_check"),
      ]),
    }, { additionalProperties: false }), { minItems: 1 }),
  }, { additionalProperties: false }))),
  unresolved_items: Type.Optional(Type.Array(Type.Object({
    type: Type.String({ minLength: 1 }),
    title: Type.Optional(Type.String()),
    description: Type.Optional(Type.String()),
    reason: Type.Optional(Type.String()),
    status: Type.Optional(Type.String()),
    severity: Type.Optional(Type.Union([
      Type.Literal("low"), Type.Literal("medium"), Type.Literal("high"),
      Type.Literal("critical"), Type.Literal("blocker"), Type.Literal("error"),
    ])),
    blocking: Type.Optional(Type.Boolean()),
    request_id: Type.Optional(Type.String()),
    step_id: Type.Optional(Type.String()),
    wire_path: Type.Optional(Type.String()),
    evidence_refs: Type.Optional(Type.Array(Type.String({ minLength: 1 }))),
  }, { additionalProperties: false }))),
}, { additionalProperties: false });

const RecordingPlan = Type.Object({
  semantic_plan: Type.Optional(SemanticPlan),
  ops: Type.Optional(Type.Array(LiveRecordingOperation)),
}, { additionalProperties: false });

const RecordingPlanArgument = Type.Union([
  RecordingPlan,
  Type.String({ minLength: 2 }),
]);

const RecordingScalarAssertion = Type.Object({
  path: Type.Optional(Type.String()),
  response_path: Type.Optional(Type.String()),
  operator: Type.Optional(Type.Union([
    Type.Literal("equals"), Type.Literal("eq"), Type.Literal("not_equals"),
    Type.Literal("ne"), Type.Literal("contains"), Type.Literal("exists"),
    Type.Literal("truthy"),
  ])),
  equals: Type.Optional(Type.Any()),
  value: Type.Optional(Type.Any()),
  equals_input: Type.Optional(Type.String()),
  input_path: Type.Optional(Type.String()),
}, { additionalProperties: false, minProperties: 1 });

const RecordingCountAssertion = Type.Object({
  verify_records_min_count: Type.Integer({ minimum: 0 }),
}, { additionalProperties: false });

const RecordingCollectionAssertion = Type.Object({
  collection_path: Type.String({ minLength: 1 }),
  where: Type.Record(Type.String({ minLength: 1 }), Type.Union([
    Type.Object({ equals_input: Type.String({ minLength: 1 }) }, { additionalProperties: false }),
    Type.Object({ equals: Type.Any() }, { additionalProperties: false }),
  ]), { minProperties: 1 }),
  min_matches: Type.Integer({ minimum: 1 }),
}, { additionalProperties: false });

const RecordingAssertion = Type.Union([
  RecordingScalarAssertion,
  RecordingCountAssertion,
  RecordingCollectionAssertion,
]);

const ReplayOverrides = Type.Object({
  url_path: Type.Optional(Type.String()),
  query: Type.Optional(Type.Record(Type.String(), Type.Any())),
  body: Type.Optional(Type.Record(Type.String(), Type.Any())),
  headers: Type.Optional(Type.Record(Type.String(), Type.Any())),
}, { additionalProperties: false });

export const recordingTools = [
  proxyTool({
    name: "get_recording_state",
    label: "读取录制状态",
    description:
      "读取当前权威且已脱敏的录制事实、请求图、FlowSpec、人工编辑和待确认项。规划前必须调用；不要凭会话记忆猜测当前状态。",
    parameters: Type.Object(RecordingIdentity, { additionalProperties: false }),
  }),
  proxyTool({
    name: "get_recording_delta",
    label: "读取实时录制增量",
    description: "按 since_seq 分页拉取新增且已脱敏的请求、页面事件和启发式候选。实时分析时必须以该事实增量为准；若 has_more=true，继续用 next_seq 读取，直到 has_more=false。大响应中的 __truncated_* 标记表示投影已裁剪，不表示原始事实缺失。",
    parameters: Type.Object({
      ...RecordingIdentity,
      since_seq: Type.Optional(Type.Integer({ minimum: 0 })),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 50 })),
    }, { additionalProperties: false }),
  }),
  proxyTool({
    name: "ask_operator",
    label: "询问录制操作人",
    description: "仅在录制现场确有业务歧义且无法由事实自答时询问一个问题；严禁询问 recording_id、flow_version、run_id 等后端内部字段；60 秒无回答会返回 answered=false，不得阻塞后续判断。",
    parameters: Type.Object({
      ...RecordingIdentity,
      text: Type.String({ minLength: 1 }),
      options: Type.Optional(Type.Array(Type.String({ minLength: 1 }))),
      context_ref: Type.Optional(Type.String()),
    }, { additionalProperties: false }),
  }),
  proxyTool({
    name: "replay_request",
    label: "重放录制请求",
    description: "按 request_id 重放当前录制中的一个请求。鉴权由执行器代持，返回内容已脱敏并附带后端生成的 verification_id。",
    parameters: Type.Object({
      ...RecordingIdentity,
      request_id: Type.String({ minLength: 1 }),
      overrides: Type.Optional(ReplayOverrides),
    }, { additionalProperties: false }),
  }),
  proxyTool({
    name: "perturb_replay",
    label: "扰动重放请求链",
    description: "顺序重放请求链并只扰动第一条请求。perturb 直接填写 url_path/query/body/headers，禁止按 request_id 再包一层；返回响应差异和执行器生成的 verification_id。",
    parameters: Type.Object({
      ...RecordingIdentity,
      chain_request_ids: Type.Array(Type.String({ minLength: 1 }), { minItems: 1 }),
      perturb: ReplayOverrides,
    }, { additionalProperties: false }),
  }),
  proxyTool({
    name: "verify_dependency",
    label: "验证步骤依赖",
    description: "按 FlowSpec 中已提议的 link_id 执行来源步骤、提取响应值并注入目标步骤。步骤、字段路径和签名全部由执行器读取并签发，模型不得自行提交请求链或伪造验证证据。",
    parameters: Type.Object({
      ...RecordingIdentity,
      link_id: Type.String({ minLength: 1 }),
    }, { additionalProperties: false }),
  }),
  proxyTool({
    name: "execute_write_with_verify",
    label: "执行写入并读回验证",
    description: "真实执行指定写步骤，等待后重放 verify 读请求、执行确定性断言并可选清理；返回执行器签发的 verification_id。断言必须使用声明字段：可用 path/response_path 配合 operator 与 equals/equals_input，或使用 verify_records_min_count 校验读回记录数量；未知字段会被拒绝。",
    parameters: Type.Object({
      ...RecordingIdentity,
      write_step_id: Type.String({ minLength: 1 }),
      inputs: Type.Record(Type.String(), Type.Any()),
      verify_request_id: Type.String({ minLength: 1 }),
      assertion: RecordingAssertion,
      cleanup_request_id: Type.Optional(Type.String({ minLength: 1 })),
    }, { additionalProperties: false }),
  }),
  proxyTool({
    name: "browser_navigate",
    label: "验证浏览器导航",
    description: "使用录制会话仍存活的浏览器导航到 http(s) 页面，期间网络继续进入录制事实。",
    parameters: Type.Object({
      ...RecordingIdentity,
      url: Type.String({ minLength: 1 }),
    }, { additionalProperties: false }),
  }),
  proxyTool({
    name: "browser_snapshot",
    label: "读取验证页面快照",
    description: "读取当前页可交互元素的 role/name/text 与下拉选项语义快照，并返回 enum_snapshot verification_id。",
    parameters: Type.Object(RecordingIdentity, { additionalProperties: false }),
  }),
  ...["click", "fill", "select"].map((kind) => proxyTool({
    name: `browser_${kind}`,
    label: `验证浏览器${kind}`,
    description: "用 role+name 或 text 语义定位执行浏览器动作；禁止使用坐标和任意 CSS 选择器。",
    parameters: Type.Object({
      ...RecordingIdentity,
      locator: Type.Object({
        role: Type.Optional(Type.String({ minLength: 1 })),
        name: Type.Optional(Type.String()),
        text: Type.Optional(Type.String({ minLength: 1 })),
        exact: Type.Optional(Type.Boolean()),
      }, { additionalProperties: false }),
      ...(kind === "click" ? {} : { value: Type.Any() }),
    }, { additionalProperties: false }),
  })),
  proxyTool({
    name: "list_link_candidates",
    label: "读取值依赖候选",
    description: "扫描任意响应叶子到后续请求路径、查询、请求体或非敏感请求头的强值候选，不直接修改 FlowSpec。",
    parameters: Type.Object(RecordingIdentity, { additionalProperties: false }),
  }),
  proxyTool({
    name: "get_verification",
    label: "读取验证记录",
    description: "按 verification_id 读取执行器生成且已脱敏的验证证据。",
    parameters: Type.Object({
      ...RecordingIdentity,
      verification_id: Type.String({ minLength: 1 }),
    }, { additionalProperties: false }),
  }),
  proxyTool({
    name: "submit_recording_plan",
    label: "提交录制规划",
    description:
      "提交当前录制版本的严格类型语义增量。字段操作必须使用 request_id 或 step_id 加规范 wire_path，并放入 plan.ops；名称、来源、required、枚举不得写入 semantic_plan。semantic_plan 只允许 business_understanding、capabilities、unresolved_items；capability 必须提供 name、title、kind、anchor_step_id 和带 execute/preflight/option_source/fact_check usage 的 request_refs，禁止 steps、id、fields、dependencies、enums 等旧别名。request_refs 仅表达模型观察，后端会从 anchor 和已验证依赖图重新编译实际成员，模型不能强行加入无关请求。set_param_source 六分类为 user_input、constant、session_header、page_context、chained、computed，分类必须可编译并有录制证据。依赖只能先用 propose_dependency 提案，禁止直接标 verified。提交后必须检查 op_results；deferred、rejected、rolled_back 都没有完整落地，必须按 reason 和 must_retry 修正后读取新版本重试。禁止提交 FlowSpec；后端负责事实、版本和安全准入。",
    parameters: Type.Object(
      {
        ...RecordingIdentity,
        base_flow_version: Type.Integer({ minimum: 0 }),
        plan: Type.Optional(RecordingPlanArgument),
      },
      // Models sometimes flatten explanations beside `plan`; these are
      // stripped by sanitizeRecordingToolParams before the backend call.
      { additionalProperties: true },
    ),
  }),
  proxyTool({
    name: "get_validation_report",
    label: "读取验证报告",
    description:
      "读取当前 FlowSpec 的最新确定性验证报告。修复前必须调用，以后端报告而不是会话中的旧错误为准。",
    parameters: Type.Object(RecordingIdentity, { additionalProperties: false }),
  }),
  proxyTool({
    name: "submit_recording_repair",
    label: "提交录制修复",
    description:
      "提交针对最新验证报告的白名单修复操作。后端负责版本检查、操作白名单、应用和重新验证。",
    parameters: Type.Object(
      {
        ...RecordingIdentity,
        base_flow_version: Type.Integer({ minimum: 0 }),
        operations: Type.Array(LiveRecordingOperation),
      },
      { additionalProperties: false },
    ),
  }),
  proxyTool({
    name: "submit_skill_docs",
    label: "提交自包含 Skill 文档",
    description:
      "基于当前 FlowSpec 事实提交完整 SKILL.md 与 reference.md 正文。API chain 必须逐条标 verification_id 或 unverified，不得包含凭证。",
    parameters: Type.Object({
      ...RecordingIdentity,
      skill_md: Type.String({ minLength: 1 }),
      reference_md: Type.String({ minLength: 1 }),
    }, { additionalProperties: false }),
  }),
  proxyTool({
    name: "submit_recording_review",
    label: "提交发布审核",
    description:
      "提交当前录制版本的验收、安全、合规审核候选。后端发布闸门拥有最终决定权。",
    parameters: Type.Object(
      {
        ...RecordingIdentity,
        base_flow_version: Type.Integer({ minimum: 0 }),
        review: Type.Object(
          {
            acceptance: Type.Object({
              passed: Type.Boolean(),
              reasons: Type.Optional(Type.Array(Type.String())),
            }, { additionalProperties: false }),
            security: Type.Object({
              passed: Type.Boolean(),
              reasons: Type.Optional(Type.Array(Type.String())),
            }, { additionalProperties: false }),
            compliance: Type.Object({
              passed: Type.Boolean(),
              reasons: Type.Optional(Type.Array(Type.String())),
            }, { additionalProperties: false }),
            blocking_reasons: Type.Optional(Type.Array(Type.String())),
          },
          { additionalProperties: false },
        ),
      },
      { additionalProperties: false },
    ),
  }),
];
