/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 这些工具只提供事实读取和结果保存，不分析、不归类、不改写。
 */

import { SUBMIT_RECORDING_RESULT } from "./result-gate.mjs";
import { logPiOnly } from "./policy.mjs";
import { summarizeToolArgs, summarizeToolResult } from "./pi-trace.mjs";

function toolText(payload) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    details: payload,
  };
}

async function responseView(evidence, recordingId, response) {
  const body = response.payload?.body && typeof response.payload.body === "object"
    ? { ...response.payload.body }
    : null;
  const view = {
    seq: response.seq,
    status: response.payload?.status,
    body,
  };
  if (body?.stored === "blob" && body.blob_id) {
    try {
      const slice = await evidence.readBlob(recordingId, body.blob_id, { offset: 0, length: 8000 });
      view.body = {
        ...body,
        text: Buffer.from(slice.bytes).toString("utf8"),
        preview_bytes: slice.bytes.byteLength,
        has_more: slice.hasMore,
        total_bytes: slice.totalBytes,
      };
    } catch {
      // 文件不在就只回元数据，不编造正文
    }
  }
  return view;
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
      const requestId = event.payload?.request_id;
      if (event.kind === "network_request" && requestId) {
        const response = await evidence.findResponseForRequest(recordingId, requestId);
        if (response) {
          return {
            found: true,
            event,
            response: await responseView(evidence, recordingId, response),
          };
        }
      }
      return { found: true, event };
    },
    async read_response_blob({ blob_id, offset = 0, length = 65536 }) {
      const start = Number(offset) || 0;
      const maxLen = Math.min(1024 * 1024, Math.max(1, Number(length) || 65536));
      const stored = await evidence.findStoredBody(recordingId, blob_id);
      if (stored?.body?.stored === "inline") {
        const text = String(stored.body.text || "");
        const slice = text.slice(start, start + maxLen);
        return {
          found: true,
          stored: "inline",
          blob_id: stored.body.blob_id || "",
          request_id: stored.event?.payload?.request_id || "",
          text: slice,
          offset: start,
          total_bytes: text.length,
          has_more: start + slice.length < text.length,
        };
      }
      const fileId = stored?.body?.blob_id || blob_id;
      try {
        const slice = await evidence.readBlob(recordingId, fileId, { offset: start, length: maxLen });
        return {
          found: true,
          stored: "blob",
          blob_id: slice.blobId,
          offset: slice.offset,
          total_bytes: slice.totalBytes,
          has_more: slice.hasMore,
          bytes_base64: Buffer.from(slice.bytes).toString("base64"),
        };
      } catch (error) {
        if (error?.code === "ENOENT") {
          return {
            found: false,
            blob_id,
            error: "没有这个正文。请读 network_response 的 payload.body.text，或把 body.blob_id（blob_ 开头）传给本工具。不要把 request_id 当成 blob_id。",
          };
        }
        throw error;
      }
    },
    async read_screenshot({ blob_id, offset = 0, length = 65536 }) {
      const result = await this.read_response_blob({ blob_id, offset, length });
      if (result.found) return result;
      const index = await evidence.index(recordingId);
      const shots = index.items.filter((item) => item.kind === "screenshot" && item.blob_id);
      return {
        found: false,
        blob_id,
        error: shots.length
          ? `没有这张截图。可用 blob_id：${shots.map((item) => item.blob_id).join(", ")}`
          : "这场录制没有截图。不要编造 blob_id，用 interaction 和请求正文即可。",
      };
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
      description: "按序号列出 interaction、xhr/fetch、network_response、visible_control、截图和页面跳转。visible_control 是当前页看得见的筛选/表单/表格控件事实，含日期区间、下拉、树、页签、分段器、上传。只投影已有字段，不分类、不判断能力。",
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
      description: "读取指定序号的原始证据。读 network_request 时会附带对应响应的 status 和 body；大正文在 body.text 预览或 body.blob_id。",
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
      description: "按偏移量读取原始响应体。只接受 body.blob_id（blob_ 开头）。不要把 request_id 当 blob_id；小 JSON 直接读 network_response 或请求附带的 response.body.text。",
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

export function wrapPiToolsForSdk(host, defineTool, Type, trace = null) {
  const specs = describePiTools();
  return specs.map((spec) => defineTool({
    name: spec.name,
    label: spec.label,
    description: spec.description,
    promptSnippet: spec.description,
    parameters: toTypeBox(spec.parameters, Type),
    execute: async (_id, params) => {
      const args = params || {};
      const started = Date.now();
      if (trace?.recordToolStart) trace.recordToolStart(spec.name, args);
      else logPiOnly(`[PI分析] 调用 ${spec.name} ${summarizeToolArgs(spec.name, args)}`);
      try {
        const result = await host[spec.name](args);
        const summary = summarizeToolResult(spec.name, result);
        if (trace) trace.recordTool(spec.name, args, summary, true);
        else logPiOnly(`[PI分析] 工具完成 ${spec.name} ${Date.now() - started}ms → ${summary}`);
        return toolText(result);
      } catch (error) {
        const message = error?.message || String(error);
        if (trace) trace.recordTool(spec.name, args, message, false);
        else logPiOnly(`[PI分析] 工具失败 ${spec.name} ${Date.now() - started}ms → ${message}`);
        throw error;
      }
    },
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
