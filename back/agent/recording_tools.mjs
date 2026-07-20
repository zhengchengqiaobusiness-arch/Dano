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
      if (SUBMISSION_TOOLS.has(name)) {
        const sanitizedParams = sanitizeRecordingToolParams(name, params);
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
      const output = await callRecordingTool(name, params, toolCallId);
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
  "request_roles",
  "field_semantics",
  "capabilities",
  "capability_relations",
  "unresolved_items",
];

function asSemanticArray(value) {
  if (Array.isArray(value)) return value.filter((item) => item && typeof item === "object");
  return value && typeof value === "object" ? [value] : [];
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
    (key) => rawSemantic[key] !== undefined || value[key] !== undefined,
  );
  for (const key of SEMANTIC_PLAN_KEYS) {
    if (semantic[key] === undefined && value[key] !== undefined) {
      semantic[key] = value[key];
    }
  }
  semantic.business_understanding = (
    typeof semantic.business_understanding === "string"
    || (
      semantic.business_understanding
      && typeof semantic.business_understanding === "object"
      && !Array.isArray(semantic.business_understanding)
    )
  ) ? semantic.business_understanding : {};
  semantic.request_roles = asSemanticArray(semantic.request_roles);
  semantic.field_semantics = asSemanticArray(semantic.field_semantics);
  semantic.capabilities = [
    ...asSemanticArray(semantic.capabilities),
    ...asSemanticArray(semantic.item),
    ...asSemanticArray(value.item),
    ...asSemanticArray(value.capability),
  ];
  semantic.capability_relations = asSemanticArray(semantic.capability_relations);
  semantic.unresolved_items = asSemanticArray(semantic.unresolved_items);
  delete semantic.item;
  delete semantic.capability;
  return normalizeConfidenceDeep({
    _submitted_semantic_keys: submittedSemanticKeys,
    semantic_plan: semantic,
    ops: Array.isArray(value.ops) ? value.ops : [],
  });
}

export function sanitizeRecordingToolParams(name, params) {
  if (name !== "submit_recording_plan" || !params || typeof params !== "object") return params;
  const allowed = ["recording_id", "flow_version", "base_flow_version", "plan"];
  const sanitized = Object.fromEntries(
    allowed.filter((key) => key in params).map((key) => [key, params[key]]),
  );
  sanitized.plan = canonicalizeRecordingPlan(sanitized.plan);
  return sanitized;
}


const RecordingIdentity = {
  recording_id: Type.String({ minLength: 1 }),
  flow_version: Type.Optional(Type.Integer({ minimum: 0 })),
};

// The SDK validates tool arguments before execute/sanitization. Keep this
// boundary object-shaped but tolerant, then canonicalize deterministically and
// let the backend enforce the complete semantic/fact contract.
const RecordingPlan = Type.Object({}, { additionalProperties: true });

export const recordingTools = [
  proxyTool({
    name: "get_recording_state",
    label: "读取录制状态",
    description:
      "读取当前权威且已脱敏的录制事实、请求图、FlowSpec、人工编辑和待确认项。规划前必须调用；不要凭会话记忆猜测当前状态。",
    parameters: Type.Object(RecordingIdentity, { additionalProperties: false }),
  }),
  proxyTool({
    name: "submit_recording_plan",
    label: "提交录制规划",
    description:
      "提交基于当前录制版本生成的完整语义规划候选。plan 必须直接包含 semantic_plan（其内包含 business_understanding、request_roles、field_semantics、capabilities、capability_relations、unresolved_items）和可选 ops；禁止提交 plan.flow_spec 或完整 FlowSpec。field_semantics 必须用真实 step_id + wire_path 关联录制字段，并给出 public_name、business_type、category、source_kind、数值 confidence（0 到 1）、evidence。入口会修复常见字段摊平并归一化置信度，后端会做 Schema、事实和版本校验；不得改写原始请求事实。",
    parameters: Type.Object(
      {
        ...RecordingIdentity,
        base_flow_version: Type.Integer({ minimum: 0 }),
        plan: RecordingPlan,
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
        operations: Type.Array(Type.Record(Type.String(), Type.Any())),
      },
      { additionalProperties: false },
    ),
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
              model_id: Type.Optional(Type.String({ minLength: 1 })),
            }, { additionalProperties: false }),
            security: Type.Object({
              passed: Type.Boolean(),
              reasons: Type.Optional(Type.Array(Type.String())),
              model_id: Type.Optional(Type.String({ minLength: 1 })),
            }, { additionalProperties: false }),
            compliance: Type.Object({
              passed: Type.Boolean(),
              reasons: Type.Optional(Type.Array(Type.String())),
              model_id: Type.Optional(Type.String({ minLength: 1 })),
            }, { additionalProperties: false }),
          },
          { additionalProperties: false },
        ),
      },
      { additionalProperties: false },
    ),
  }),
];
