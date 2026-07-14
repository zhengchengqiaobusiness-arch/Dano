// Recording-only Pi tools. Every tool is a thin authenticated proxy to Dano.
// The authoritative recording state and all mutations remain in Python.
import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const BASE_URL = process.env.DANO_AGENT_BASE_URL;
const TOKEN = process.env.DANO_AGENT_TOKEN;
const RUN_ID = process.env.DANO_AGENT_RUN_ID;

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
    execute: async (toolCallId, params) => {
      const output = await callRecordingTool(name, params, toolCallId);
      return {
        content: [{ type: "text", text: JSON.stringify(output) }],
        isError: false,
      };
    },
  });
}

const RecordingIdentity = {
  recording_id: Type.String({ minLength: 1 }),
  flow_version: Type.Optional(Type.Integer({ minimum: 0 })),
};

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
      "提交基于当前录制版本生成的完整语义规划候选。后端会做 Schema、事实和版本校验；不得改写原始请求事实。",
    parameters: Type.Object(
      {
        ...RecordingIdentity,
        base_flow_version: Type.Integer({ minimum: 0 }),
        plan: Type.Record(Type.String(), Type.Any()),
      },
      { additionalProperties: false },
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
            acceptance: Type.Record(Type.String(), Type.Any()),
            security: Type.Record(Type.String(), Type.Any()),
            compliance: Type.Record(Type.String(), Type.Any()),
            blocking_reasons: Type.Optional(Type.Array(Type.String())),
          },
          { additionalProperties: false },
        ),
      },
      { additionalProperties: false },
    ),
  }),
];
