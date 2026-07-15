import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const pending = new Map();
let emitMessage = () => { throw new Error("tool bridge not initialized"); };
let currentSession = () => { throw new Error("tool bridge has no session context"); };

export function configureToolBridge({ emit, getSession }) {
  emitMessage = emit;
  currentSession = getSession;
}

export function resolveToolResult(message) {
  const entry = pending.get(message.call_id);
  if (!entry) return false;
  pending.delete(message.call_id);
  clearTimeout(entry.timer);
  if (message.ok) entry.resolve(message.result || {});
  else entry.reject(new Error(message.error || "recording tool failed"));
  return true;
}

function bridge(name, params, toolCallId) {
  const sessionId = currentSession();
  const callId = `${sessionId}:${toolCallId}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(callId);
      reject(new Error(`tool timeout: ${name}`));
    }, 120000);
    pending.set(callId, { resolve, reject, timer });
    emitMessage({ type: "tool_request", call_id: callId, session_id: sessionId, tool: name, params });
  });
}

function proxy(name, label, description, parameters) {
  return defineTool({
    name, label, description, parameters,
    execute: async (toolCallId, params) => ({
      content: [{ type: "text", text: JSON.stringify(await bridge(name, params, toolCallId)) }],
      isError: false,
    }),
  });
}

const semanticOperation = Type.Object({
  op: Type.Union([
    Type.Literal("set_field_axis"), Type.Literal("link_field_binding"),
    Type.Literal("unlink_field_binding"), Type.Literal("create_capability"),
    Type.Literal("delete_capability"), Type.Literal("merge_capabilities"),
    Type.Literal("split_capability"), Type.Literal("move_request_to_capability"),
    Type.Literal("set_input_schema"), Type.Literal("set_output_schema"),
    Type.Literal("set_capability_name"), Type.Literal("set_capability_description"),
  ]),
  target_uuid: Type.String({ minLength: 1 }),
  axis: Type.Optional(Type.String()),
  value: Type.Optional(Type.Any()),
  evidence_ids: Type.Array(Type.String({ minLength: 1 }), { minItems: 1 }),
  confidence: Type.Number({ minimum: 0, maximum: 1 }),
  expected_revision: Type.Number({ minimum: 0 }),
});

export const recordingTools = [
  proxy("list_transactions", "列出事务", "列出业务事务及其稳定 UUID，不返回原始响应。", Type.Object({})),
  proxy("get_transaction", "读取事务", "按 UUID 读取一个业务事务的脱敏证据。", Type.Object({ transaction_uuid: Type.String() })),
  proxy("get_request_response", "读取请求响应合同", "读取请求/响应 schema、状态和证据引用，不返回原始值。", Type.Object({ request_uuid: Type.String() })),
  proxy("trace_control", "追踪控件", "按控件证据 UUID 追踪 DOM 到 wire 绑定。", Type.Object({ control_uuid: Type.String() })),
  proxy("trace_field", "追踪字段", "按永久 field UUID 读取逐轴决定和证据。", Type.Object({ field_uuid: Type.String() })),
  proxy("trace_submit_path", "追踪提交路径", "追踪字段从 provider 到终点请求的路径。", Type.Object({ field_uuid: Type.String() })),
  proxy("get_enum_evidence", "读取枚举证据", "按 field UUID 读取覆盖度和动态 resolver 合同。", Type.Object({ field_uuid: Type.String() })),
  proxy("search_js_binding", "检索静态绑定", "只检索静态 AST/SourceMap 绑定摘要，不读取原始 JavaScript。", Type.Object({ query: Type.String(), field_uuid: Type.Optional(Type.String()) })),
  proxy("list_unbound_requests", "列出未绑定请求", "列出 CaptureStore 中尚未进入能力的业务请求。", Type.Object({})),
  proxy("get_validation_report", "读取校验", "读取确定性校验报告。", Type.Object({})),
  proxy("apply_semantic_operations", "原子提交语义变更", "每轮恰好调用一次。服务端原子校验证据、UUID、manual axis 和 revision。", Type.Object({ operations: Type.Array(semanticOperation), expected_revision: Type.Number({ minimum: 0 }) })),
  proxy("submit_recording_review", "提交录制审核", "提交当前隔离审核角色的结论。", Type.Object({ passed: Type.Boolean(), reasons: Type.Array(Type.String()), evidence: Type.Optional(Type.Array(Type.String())), expected_revision: Type.Number() })),
];
