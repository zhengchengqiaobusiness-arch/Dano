/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 这些工具只提供事实读取和结果保存，不分析、不归类、不改写。
 */

import { SUBMIT_RECORDING_RESULT } from "./result-gate.mjs";

function toolText(payload) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    details: payload,
  };
}

export function createPiToolHost({
  recordingId,
  evidence,
  files,
  gate,
  getPiSessionId,
}) {
  return {
    async list_recording_manifest() {
      return evidence.snapshot(recordingId);
    },
    async list_recording_index() {
      const index = await evidence.index(recordingId);
      return {
        recording_id: recordingId,
        count: index.count,
        items: index.items,
      };
    },
    async read_evidence_delta({ after_seq = 0, limit = 20 } = {}) {
      const events = await evidence.read(recordingId, {
        afterSeq: Number(after_seq) || 0,
        limit: Math.min(100, Math.max(1, Number(limit) || 20)),
      });
      const session = evidence.snapshot(recordingId);
      return {
        recording_id: recordingId,
        after_seq: Number(after_seq) || 0,
        next_seq: events.length ? events[events.length - 1].seq : Number(after_seq) || 0,
        has_more: session.lastSeq > (events.length ? events[events.length - 1].seq : Number(after_seq) || 0),
        events,
      };
    },
    async read_evidence_item({ seq }) {
      const event = await evidence.readOne(recordingId, seq);
      if (!event) return { found: false, seq };
      return { found: true, event };
    },
    async read_response_blob({ blob_id, offset = 0, length = 65536 }) {
      const slice = await evidence.readBlob(recordingId, blob_id, {
        offset: Number(offset) || 0,
        length: Math.min(1024 * 1024, Math.max(1, Number(length) || 65536)),
      });
      return {
        blob_id: slice.blobId,
        offset: slice.offset,
        total_bytes: slice.totalBytes,
        has_more: slice.hasMore,
        bytes_base64: Buffer.from(slice.bytes).toString("base64"),
      };
    },
    async read_screenshot({ blob_id, offset = 0, length = 65536 }) {
      return this.read_response_blob({ blob_id, offset, length });
    },
    async get_recording_freeze_state() {
      const session = evidence.snapshot(recordingId);
      return {
        recording_id: recordingId,
        frozen: Boolean(session.frozen),
        frozen_at: session.frozenAt || "",
        evidence_count: session.evidenceCount,
        last_seq: session.lastSeq,
        has_final_result: Boolean(session.hasFinalResult),
      };
    },
    async submit_recording_draft({ draft }) {
      if (!draft || typeof draft !== "object" || Array.isArray(draft)) {
        throw new Error("draft 必须是非空对象，且不得冒充最终结果");
      }
      const payload = {
        recording_id: recordingId,
        saved_at: new Date().toISOString(),
        draft: structuredClone(draft),
      };
      await files.writeDraft(recordingId, payload);
      return { saved: true, final: false };
    },
    async [SUBMIT_RECORDING_RESULT]({ recording_id, final, result }) {
      const session = evidence.snapshot(recordingId);
      return gate.submitRecordingResult({
        recordingId: recording_id,
        expectedRecordingId: recordingId,
        callerSessionId: getPiSessionId(),
        expectedSessionId: session.piSessionId,
        final,
        result,
        frozen: Boolean(session.frozen),
      });
    },
  };
}

export function describePiTools() {
  return [
    {
      name: "list_recording_manifest",
      label: "录制清单",
      description: "读取当前录制清单与状态。只返回已保存事实，不做业务判断。",
      parameters: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "list_recording_index",
      label: "证据索引",
      description: "按序号列出 interaction、xhr/fetch 请求和页面跳转的短摘要。只投影已有字段，不分类、不判断能力、不丢后半场。",
      parameters: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "read_evidence_delta",
      label: "证据增量",
      description: "按序号读取证据增量。不分类、不丢弃、不改写。",
      parameters: {
        type: "object",
        properties: {
          after_seq: { type: "integer" },
          limit: { type: "integer" },
        },
        additionalProperties: false,
      },
    },
    {
      name: "read_evidence_item",
      label: "指定证据",
      description: "读取指定序号的原始证据。",
      parameters: {
        type: "object",
        properties: { seq: { type: "integer" } },
        required: ["seq"],
        additionalProperties: false,
      },
    },
    {
      name: "read_response_blob",
      label: "分段读响应体",
      description: "按偏移量读取原始响应体或其它二进制证据。",
      parameters: {
        type: "object",
        properties: {
          blob_id: { type: "string" },
          offset: { type: "integer" },
          length: { type: "integer" },
        },
        required: ["blob_id"],
        additionalProperties: false,
      },
    },
    {
      name: "read_screenshot",
      label: "读取截图",
      description: "按偏移量读取页面截图或快照二进制。",
      parameters: {
        type: "object",
        properties: {
          blob_id: { type: "string" },
          offset: { type: "integer" },
          length: { type: "integer" },
        },
        required: ["blob_id"],
        additionalProperties: false,
      },
    },
    {
      name: "get_recording_freeze_state",
      label: "冻结状态",
      description: "查看当前录制是否已经冻结。",
      parameters: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "submit_recording_draft",
      label: "过程草稿",
      description: "保存过程草稿。草稿绝不能被当成最终结果。",
      parameters: {
        type: "object",
        properties: { draft: { type: "object" } },
        required: ["draft"],
        additionalProperties: false,
      },
    },
    {
      name: SUBMIT_RECORDING_RESULT,
      label: "最终结果",
      description: "唯一最终提交入口。只允许在证据冻结后提交完整 result。系统原样保存，不会补齐或修改。result 必须是现有录制页能直接渲染的 draft：每个独立动作一项能力；capability_id 不重复；每个能力恰好一个不共用的 execute；request_refs 为 {step_id,usage} 对象；steps[].params 为含 key/path 的数组；调用方字段写在 capability.input_schema.properties；禁止只写 capabilities[].fields。",
      parameters: {
        type: "object",
        properties: {
          recording_id: { type: "string" },
          final: { type: "boolean" },
          result: { type: "object" },
        },
        required: ["recording_id", "final", "result"],
        additionalProperties: false,
      },
    },
  ];
}

export function wrapPiToolsForSdk(host, defineTool, Type) {
  const specs = describePiTools();
  return specs.map((spec) => defineTool({
    name: spec.name,
    label: spec.label,
    description: spec.description,
    promptSnippet: spec.description,
    parameters: toTypeBox(spec.parameters, Type),
    execute: async (_id, params) => toolText(await host[spec.name](params || {})),
  }));
}

function toTypeBox(schema, Type) {
  const properties = schema.properties || {};
  const shape = {};
  for (const [key, value] of Object.entries(properties)) {
    if (value.type === "integer") shape[key] = Type.Integer();
    else if (value.type === "boolean") shape[key] = Type.Boolean();
    else if (value.type === "object") shape[key] = Type.Object({}, { additionalProperties: true });
    else shape[key] = Type.String();
  }
  return Type.Object(shape, {
    additionalProperties: false,
    required: schema.required || [],
  });
}
