/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 这些工具只提供事实读取和结果保存，不分析、不归类、不改写。
 */

import { SUBMIT_RECORDING_RESULT, assertCapabilityIdentityContract, assertPageDisplayContract } from "./result-gate.mjs";
import { logPiOnly } from "./policy.mjs";
import { summarizeToolArgs, summarizeToolResult } from "./pi-trace.mjs";
import { buildActionTimeline, requestShapeFromEvents } from "./evidence-facts.mjs";
import { projectVisibleControlSnapshot } from "./visible-controls.mjs";
import { mergeCapabilityIntoDraft } from "./result-merge.mjs";
import { isNoiseNetworkPath } from "./browser-actions.mjs";
import { projectContractToRequest } from "./contract-project.mjs";
import {
  writeSkillArtifact,
  readSkillArtifact,
  validateSkillPackage,
  runIsolatedScript,
  readPageAsset,
} from "./skill-package-tools.mjs";
import { readGeneratorGuides } from "./skill-export/read-guides.mjs";

const SCREENSHOT_TEXT_ONLY_NOTE = "默认不把截图写入对话。需要看图时 screenshot/read_screenshot 设 as_image=true。";

export function parseStructuredToolValue(value) {
  if (typeof value !== "string") return value;
  const text = value.trim();
  if (!text || (text[0] !== "{" && text[0] !== "[")) return value;
  try {
    return JSON.parse(text);
  } catch {
    return value;
  }
}

export function coerceStructuredToolArgs(args, schema) {
  if (!args || typeof args !== "object" || Array.isArray(args)) return args;
  const properties = schema?.properties || {};
  const next = { ...args };
  for (const [key, spec] of Object.entries(properties)) {
    if (!(key in next)) continue;
    if (spec?.type === "object" || spec?.type === "array") {
      next[key] = parseStructuredToolValue(next[key]);
    }
  }
  return next;
}

export function compactInspect(shot) {
  if (!shot || typeof shot !== "object") return shot;
  const { screenshot, frames, ...rest } = shot;
  return {
    ...rest,
    controls: Array.isArray(rest.controls) ? rest.controls : [],
    actions: Array.isArray(rest.actions) ? rest.actions : [],
    options: Array.isArray(rest.options) ? rest.options : [],
    recentUserActions: Array.isArray(rest.recentUserActions) ? rest.recentUserActions : [],
    width: screenshot?.width,
    height: screenshot?.height,
    image_in_conversation: false,
  };
}

export function stripImageFromToolResult(result) {
  if (!result || typeof result !== "object") return result;
  const looksImage = result.__image === true
    || (typeof result.mimeType === "string" && result.mimeType.startsWith("image/") && result.data);
  if (!looksImage && !result.screenshot?.data) return result;
  const { data: _data, __image: _image, screenshot, ...rest } = result;
  return {
    ...rest,
    width: rest.width || screenshot?.width,
    height: rest.height || screenshot?.height,
    image_in_conversation: false,
    note: SCREENSHOT_TEXT_ONLY_NOTE,
  };
}

function toolText(payload) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    details: payload,
  };
}


function looksLikeImage(bytes) {
  const buf = Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes || []);
  if (buf.length < 3) return false;
  if (buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) return true;
  if (buf.length >= 4 && buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4e && buf[3] === 0x47) return true;
  if (buf[0] === 0x47 && buf[1] === 0x49 && buf[2] === 0x46) return true;
  return false;
}

function imageBlobMeta(blobId, totalBytes = 0) {
  return {
    found: true,
    stored: "image",
    blob_id: blobId,
    total_bytes: Number(totalBytes) || 0,
    readable: false,
    error: "这是页面截图，不是请求正文。不要当文本读，也不要分段拉二进制。控件用 visible_control，请求正文用 network_request / network_response。",
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

const ASSIST_HOLD_ACTIONS = new Set(["click", "fill", "select", "press", "choose", "fill_fields", "open_page"]);

function assistHoldError() {
  return {
    ok: false,
    paused: true,
    assist: true,
    human_can_click: true,
    error: "已暂停自动操作。等用户在预览做完或说继续。现在只能 snapshot / network_since / 读证据，禁止再 click / fill / choose。",
  };
}

export function createPiToolHost({
  recordingId,
  evidence,
  files,
  gate,
  getPiSessionId,
  getBrowser = null,
  getTargetUrl = null,
  freezeEvidence = null,
  onAssist = null,
  isAssistHold = null,
  onPauseForAssist = null,
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
        if (looksLikeImage(slice.bytes)) {
          return imageBlobMeta(slice.blobId, slice.totalBytes);
        }
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
    async read_screenshot({ blob_id, as_image = false }) {
      const index = await evidence.index(recordingId);
      const shots = index.items.filter((item) => item.kind === "screenshot" && item.blob_id);
      const hit = shots.find((item) => item.blob_id === blob_id);
      if (!hit) {
        return {
          found: false,
          blob_id,
          error: shots.length
            ? `没有这张截图。可用 blob_id：${shots.map((item) => item.blob_id).join(", ")}`
            : "这场录制没有截图。不要编造 blob_id。",
        };
      }
      if (as_image) {
        const bytes = await evidence.files.readBlob(recordingId, blob_id);
        return {
          found: true,
          blob_id,
          __image: true,
          as_image: true,
          mimeType: "image/png",
          data: Buffer.from(bytes).toString("base64"),
          total_bytes: bytes.length,
        };
      }
      let totalBytes = 0;
      try {
        const slice = await evidence.readBlob(recordingId, blob_id, { offset: 0, length: 16 });
        totalBytes = slice.totalBytes;
      } catch {
        // 元数据即可
      }
      return imageBlobMeta(blob_id, totalBytes);
    },
    async list_action_timeline() {
      const events = await evidence.files.readEvidence(recordingId);
      const timeline = buildActionTimeline(events);
      return {
        recording_id: recordingId,
        ...timeline,
      };
    },
    async read_request_shape({ seq }) {
      const events = await evidence.files.readEvidence(recordingId);
      return requestShapeFromEvents(events, seq);
    },
    async read_visible_controls({ seq } = {}) {
      const events = await evidence.files.readEvidence(recordingId);
      const list = Array.isArray(events) ? events : [];
      const latest = [...list].reverse().find((item) => item.kind === "visible_control");
      const target = Number(seq) || 0;
      const event = target
        ? list.find((item) => Number(item.seq) === target && item.kind === "visible_control")
        : latest;
      if (!event) return { found: false, seq: target || null };
      const snapshot = { found: true, ...projectVisibleControlSnapshot(event) };
      if (target && latest && Number(latest.seq) > Number(event.seq)) {
        snapshot.newer_seq = Number(latest.seq);
        snapshot.hint = "这不是最近一次可见控件。打开弹层、切换页签或加行后不要带旧 seq，不传 seq 或先 snapshot。";
      }
      return snapshot;
    },
    async submit_recording_capability({
      capability,
      steps = [],
      links = [],
      unresolved = [],
      capability_relations = [],
      title = "",
    } = {}) {
      const current = await files.readDraft(recordingId);
      const merged = mergeCapabilityIntoDraft(current?.draft || {}, {
        capability,
        steps,
        links,
        unresolved,
        capability_relations,
        title,
      });
      assertPageDisplayContract(merged);
      assertCapabilityIdentityContract(merged);
      await files.writeDraft(recordingId, {
        recording_id: recordingId,
        saved_at: new Date().toISOString(),
        draft: merged,
      });
      return {
        saved: true,
        final: false,
        capability_count: merged.capabilities.length,
        capability_ids: merged.capabilities.map((item) => item.capability_id),
        next_action: "该项已保存。继续按 Skill 调查或交下一项已有真实 execute 形状的能力；台账齐了用 submit_recording_result({final:true, use_draft:true})。不要写消费者包。",
      };
    },
    async control_in_app_browser({
      action = "",
      url = "",
      ref = "",
      selector = "",
      text = "",
      include_screenshot = false,
      as_image = false,
      after_seq = 0,
      fields = [],
      reason = "",
    } = {}) {
      const kind = String(action || "").trim();
      if (kind === "network_since") {
        const all = await evidence.files.readEvidence(recordingId);
        const list = Array.isArray(all) ? all : [];
        const cursor = Number(after_seq) > 0 ? Number(after_seq) : 0;
        const matched = list.filter((item) => {
          if (cursor && Number(item.seq) <= cursor) return false;
          if (item.kind === "network_request") {
            const type = String(item.payload?.resource_type || "");
            if (type && type !== "xhr" && type !== "fetch") return false;
            return !isNoiseNetworkPath(item.payload?.url || item.payload?.path || "");
          }
          return item.kind === "page_navigated" || item.kind === "interaction";
        });
        const windowed = cursor ? matched.slice(-80) : matched.slice(-40);
        return {
          after_seq: cursor || (windowed[0] ? Number(windowed[0].seq) - 1 : 0),
          events: windowed.map((item) => ({
            seq: item.seq,
            kind: item.kind,
            actor: item.payload?.actor || "",
            method: item.payload?.method || "",
            path: item.payload?.url || item.payload?.path || "",
            label: item.payload?.label || item.payload?.text || item.payload?.selector || "",
            status: item.payload?.status,
          })),
        };
      }
      if (kind === "assist") {
        const message = String(reason || "请在预览页帮忙：登录、验证码或确认写入。预览始终可以点。");
        try {
          onAssist?.({ reason: message });
        } catch {
          // 协助通知失败不得假装已经送达
        }
        try {
          onPauseForAssist?.({ reason: message });
        } catch {
          // 暂停自动点击失败仍要回协助已发出
        }
        return {
          assist: true,
          paused: true,
          message,
          human_can_click: true,
          next_action: "已暂停自动操作。等用户在预览做完或说继续，不要再 click。",
        };
      }
      if (ASSIST_HOLD_ACTIONS.has(kind) && typeof isAssistHold === "function" && isAssistHold()) {
        return assistHoldError();
      }
      const browser = typeof getBrowser === "function" ? getBrowser() : null;
      if (!browser) {
        return {
          available: false,
          error: "浏览器未打开。用 list_action_timeline / read_request_shape / read_evidence_item 读已有证据。",
        };
      }
      if (kind === "open_page") return browser.openPage?.(url) ?? { available: false, error: "当前浏览器不能打开页面" };
      if (kind === "list_pages") return browser.listPages?.() ?? { pages: [] };
      if (kind === "snapshot") {
        const shot = await browser.inspect?.({ includeScreenshot: false });
        return {
          ...compactInspect(shot),
          include_screenshot_ignored: Boolean(include_screenshot),
        };
      }
      if (kind === "screenshot") {
        const shot = await browser.inspect?.({ includeScreenshot: Boolean(as_image) });
        if (!shot || shot.error || shot.available === false) {
          return { available: false, error: shot?.error || shot?.screenshot?.error || "无法截图" };
        }
        if (as_image && shot.screenshot?.data) {
          return {
            ...compactInspect(shot),
            action: "screenshot",
            __image: true,
            as_image: true,
            mimeType: "image/png",
            data: shot.screenshot.data,
            image_in_conversation: true,
          };
        }
        return {
          ...compactInspect(shot),
          action: "screenshot",
          image_in_conversation: false,
          note: SCREENSHOT_TEXT_ONLY_NOTE,
        };
      }
      if (kind === "click" || kind === "fill" || kind === "select" || kind === "press" || kind === "choose") {
        const target = String(selector || ref || "").trim();
        return browser.actBySelector?.({ selector: target, ref: target, action: kind, text })
          ?? browser.actByRef?.({ ref: target, selector: target, action: kind, text });
      }
      if (kind === "fill_fields") {
        return browser.fillFields?.(fields);
      }
      return {
        error: `不支持的 action: ${kind || "(empty)"}。可用：open_page, list_pages, snapshot, screenshot, click, fill, select, choose, press, fill_fields, network_since, assist。`,
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
    async read_page_asset({ url }) {
      const session = evidence.snapshot(recordingId);
      return readPageAsset({
        evidence,
        recordingId,
        url,
        targetUrl: typeof getTargetUrl === "function" ? getTargetUrl() : session.targetUrl,
      });
    },
    async [SUBMIT_RECORDING_RESULT]({ recording_id, final, result, use_draft = false }) {
      let session = evidence.snapshot(recordingId);
      if (!session.frozen && typeof freezeEvidence === "function") {
        await freezeEvidence();
        session = evidence.snapshot(recordingId);
      }
      return gate.submitRecordingResult({
        recordingId: recording_id,
        expectedRecordingId: recordingId,
        callerSessionId: getPiSessionId(),
        expectedSessionId: session.piSessionId,
        final,
        result,
        use_draft,
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
      description: "按 blob 读截图。默认只回元数据；as_image=true 以图像消息送给模型。细节看 Skill 2。",
      parameters: {
        type: "object",
        properties: {
          blob_id: { type: "string" },
          as_image: { type: "boolean" },
          offset: { type: "integer" },
          length: { type: "integer" },
        },
        required: ["blob_id"],
        additionalProperties: false,
      },
    },
    {
      name: "list_action_timeline",
      label: "动作台账",
      description: "按时间列出人与 PI 点过的业务交互，以及随后出现的 xhr/fetch。带 actor=human|pi。时间接近只是候选，不划分能力。",
      parameters: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "read_request_shape",
      label: "请求形状",
      description: "摊开指定 seq 请求的 query/body 键。不判断来源，不补字段。",
      parameters: {
        type: "object",
        properties: { seq: { type: "integer" } },
        required: ["seq"],
        additionalProperties: false,
      },
    },
    {
      name: "read_visible_controls",
      label: "可见控件",
      description: "读取一场 visible_control 快照。不传 seq 则取最近一次。打开弹层后不要带旧 seq。readonly/disabled 表示整个控件不能改，不是下拉内部展示框带了原生 readonly。",
      parameters: {
        type: "object",
        properties: { seq: { type: "integer" } },
        additionalProperties: false,
      },
    },
    {
      name: "submit_recording_capability",
      label: "提交一项能力",
      description: "把一项能力写入草稿。细节看 Skill 3。",
      parameters: {
        type: "object",
        properties: {
          capability: { type: "object" },
          steps: { type: "array" },
          links: { type: "array" },
          unresolved: { type: "array" },
          capability_relations: { type: "array" },
          title: { type: "string" },
        },
        required: ["capability"],
        additionalProperties: false,
      },
    },
    {
      name: "control_in_app_browser",
      label: "Control In App Browser",
      description: "操作应用内浏览器。细节看 Skill 2。screenshot 设 as_image=true 才把图像送给模型。",
      parameters: {
        type: "object",
        properties: {
          action: { type: "string" },
          url: { type: "string" },
          ref: { type: "string" },
          selector: { type: "string" },
          text: { type: "string" },
          include_screenshot: { type: "boolean" },
          as_image: { type: "boolean" },
          after_seq: { type: "integer" },
          fields: { type: "array" },
          reason: { type: "string" },
        },
        required: ["action"],
        additionalProperties: false,
      },
    },
    {
      name: "read_page_asset",
      label: "同源前端",
      description: "读取本场已加载的同源前端资源。细节看 Skill 2。",
      parameters: {
        type: "object",
        properties: { url: { type: "string" } },
        required: ["url"],
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
      name: SUBMIT_RECORDING_RESULT,
      label: "最终结果",
      description: "唯一最终提交。use_draft=true 定稿已交能力。细节看 Skill 1。",
      parameters: {
        type: "object",
        properties: {
          recording_id: { type: "string" },
          final: { type: "boolean" },
          result: { type: "object" },
          use_draft: { type: "boolean" },
        },
        required: ["recording_id", "final"],
        additionalProperties: false,
      },
    },
  ];
}

export function createExportToolHost({
  files,
  recordingId,
  draft,
  onSubmit = null,
}) {
  const logTool = (name, detail) => {
    logPiOnly(`[出包] Skill4工具 ${name} recording_id=${recordingId || "-"} ${detail}`);
  };
  return {
    async read_generator_guides() {
      const result = await readGeneratorGuides();
      logTool("read_generator_guides", `ok=${result.ok} files=${result.files?.length || 0} error=${result.error || "-"}`);
      return result;
    },
    async read_export_contract() {
      const saved = draft || (await files.readDraft(recordingId))?.draft || {};
      const payload = {
        title: saved.title || "",
        capabilities: Array.isArray(saved.capabilities) ? saved.capabilities : [],
        steps: Array.isArray(saved.steps) ? saved.steps : [],
        links: Array.isArray(saved.links) ? saved.links : [],
        capability_relations: Array.isArray(saved.capability_relations) ? saved.capability_relations : [],
        unresolved: Array.isArray(saved.unresolved) ? saved.unresolved : [],
      };
      logTool("read_export_contract", `caps=${payload.capabilities.length} steps=${payload.steps.length} links=${payload.links.length} unresolved=${payload.unresolved.length}`);
      return payload;
    },
    async read_skill_artifact({ path: rel }) {
      try {
        const result = await readSkillArtifact(files, recordingId, rel);
        logTool("read_skill_artifact", `path=${result.path} bytes=${String(result.content || "").length}`);
        return result;
      } catch (error) {
        logTool("read_skill_artifact", `失败 path=${rel || "-"} ${error.message || error}`);
        throw error;
      }
    },
    async write_skill_artifact({ path: rel, content }) {
      try {
        const result = await writeSkillArtifact(files, recordingId, rel, content);
        logTool("write_skill_artifact", `path=${result.path} bytes=${String(content ?? "").length}`);
        return result;
      } catch (error) {
        logTool("write_skill_artifact", `失败 path=${rel || "-"} ${error.message || error}`);
        throw error;
      }
    },
    async validate_skill_package() {
      const result = await validateSkillPackage(files, recordingId, { draft });
      const errors = (result.issues || []).filter((item) => item.severity === "error");
      logTool("validate_skill_package", `ok=${result.ok} errors=${errors.length} first=${errors[0]?.code || "-"}:${errors[0]?.message || "-"}`);
      return result;
    },
    async project_contract_to_request({ capability_id, inputs = {} } = {}) {
      const saved = draft || (await files.readDraft(recordingId))?.draft || {};
      const result = projectContractToRequest(saved, capability_id, inputs);
      logTool("project_contract_to_request", `capability_id=${capability_id || "-"} ok=${result.ok} missing=${(result.missing || []).join(",") || "-"} extra=${(result.extra || []).join(",") || "-"}`);
      return result;
    },
    async run_isolated_script({ script, args = [] } = {}) {
      const result = await runIsolatedScript(files, recordingId, script, args);
      logTool("run_isolated_script", `script=${script || "-"} ok=${result.ok} code=${result.code ?? "-"} error=${result.error || "-"}`);
      return result;
    },
    async submit_skill_export({
      ok = false,
      skill_id = "",
      description = "",
      routes = [],
      errors = [],
    } = {}) {
      const payload = {
        ok: Boolean(ok),
        skill_id: String(skill_id || ""),
        description: String(description || ""),
        routes: Array.isArray(routes) ? routes : [],
        errors: Array.isArray(errors) ? errors.map((item) => String(item)) : [],
      };
      logTool("submit_skill_export", `ok=${payload.ok} skill_id=${payload.skill_id || "-"} routes=${payload.routes.length} errors=${JSON.stringify(payload.errors)}`);
      try {
        onSubmit?.(payload);
      } catch {
        // 提交回调失败仍要把结果回给模型
      }
      return { accepted: true, ...payload };
    },
  };
}

export function describeExportPiTools() {
  return [
    {
      name: "read_generator_guides",
      label: "生成规范",
      description: "读取 doc/ 下全部生成规范。写包前必须调用。缺目录或缺必需要文件则失败。",
      parameters: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "read_export_contract",
      label: "出包合同",
      description: "读取点击当下的最新合同五块：capabilities / steps / links / capability_relations / unresolved。禁止回头猜页面。",
      parameters: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "read_skill_artifact",
      label: "读 Skill 产物",
      description: "读取运输层已按合同物化的包根文件。先读再决定要不要改 SKILL.md。",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string" },
        },
        required: ["path"],
        additionalProperties: false,
      },
    },
    {
      name: "write_skill_artifact",
      label: "写 Skill 产物",
      description: "只允许覆盖 SKILL.md 的触发用语。禁止重写 client/runtime/flow/CONTRACT/INPUT_FORMS/鉴权配置，禁止另开子包。",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string" },
          content: { type: "string" },
        },
        required: ["path", "content"],
        additionalProperties: false,
      },
    },
    {
      name: "validate_skill_package",
      label: "校验 Skill 包",
      description: "跑结构/披露检查。细节看 Skill 4。",
      parameters: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "project_contract_to_request",
      label: "合同投影",
      description: "只按合同投影请求，缺键失败，不补键。细节看 Skill 4。",
      parameters: {
        type: "object",
        properties: {
          capability_id: { type: "string" },
          inputs: { type: "object" },
        },
        required: ["capability_id"],
        additionalProperties: false,
      },
    },
    {
      name: "run_isolated_script",
      label: "隔离运行",
      description: "在隔离目录运行生成的脚本。细节看 Skill 4。",
      parameters: {
        type: "object",
        properties: {
          script: { type: "string" },
          args: { type: "array" },
        },
        required: ["script"],
        additionalProperties: false,
      },
    },
    {
      name: "submit_skill_export",
      label: "提交出包",
      description: "Skill 4 验证通过后提交。ok=false 表示失败，带上 errors。不要 submit_recording_result。",
      parameters: {
        type: "object",
        properties: {
          ok: { type: "boolean" },
          skill_id: { type: "string" },
          description: { type: "string" },
          routes: { type: "array" },
          errors: { type: "array" },
        },
        required: ["ok"],
        additionalProperties: false,
      },
    },
  ];
}

export function wrapPiToolsForSdk(host, defineTool, Type, trace = null, specs = describePiTools()) {
  return specs.filter((spec) => typeof host[spec.name] === "function").map((spec) => defineTool({
    name: spec.name,
    label: spec.label,
    description: spec.description,
    promptSnippet: spec.description,
    parameters: toTypeBox(spec.parameters, Type),
    prepareArguments: (args) => coerceStructuredToolArgs(args, spec.parameters),
    execute: async (_id, params) => {
      const args = coerceStructuredToolArgs(params || {}, spec.parameters);
      const started = Date.now();
      if (trace?.recordToolStart) trace.recordToolStart(spec.name, args);
      else logPiOnly(`[PI分析] 调用 ${spec.name} ${summarizeToolArgs(spec.name, args)}`);
      try {
        const raw = await host[spec.name](args);
        if (raw?.__image && raw.data && (args.as_image === true || raw.as_image === true)) {
          const { data, mimeType, __image, screenshot, ...rest } = raw;
          const payload = { ...rest, image_in_conversation: true };
          if (trace) trace.recordTool(spec.name, args, "image", true);
          return {
            content: [
              { type: "text", text: JSON.stringify(payload) },
              { type: "image", mimeType: mimeType || "image/png", data },
            ],
            details: payload,
          };
        }
        const result = stripImageFromToolResult(raw);
        const summary = summarizeToolResult(spec.name, result);
        if (trace) trace.recordTool(spec.name, args, summary, true);
        else logPiOnly(`[PI分析] 工具完成 ${spec.name} ${Date.now() - started}ms → ${summary}`);
        return toolText(result);
      } catch (error) {
        const message = error?.message || String(error);
        if (trace) trace.recordTool(spec.name, args, message, false);
        else logPiOnly(`[PI分析] 工具失败 ${spec.name} ${Date.now() - started}ms → ${message}`);
        if (spec.name === SUBMIT_RECORDING_RESULT) {
          return toolText({
            accepted: false,
            error: message,
            next_action: "按 error 精确修正 result，保留其它已完成能力，不要重读证据，然后重新调用 submit_recording_result。",
          });
        }
        throw error;
      }
    },
  }));
}

function toTypeBox(schema, Type) {
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);
  const shape = {};
  for (const [key, value] of Object.entries(properties)) {
    let boxed;
    if (value.type === "integer") boxed = Type.Integer();
    else if (value.type === "boolean") boxed = Type.Boolean();
    else if (value.type === "object") boxed = Type.Object({}, { additionalProperties: true });
    else if (value.type === "array") boxed = Type.Array(Type.Object({}, { additionalProperties: true }));
    else boxed = Type.String();
    shape[key] = required.has(key) || typeof Type.Optional !== "function"
      ? boxed
      : Type.Optional(boxed);
  }
  return Type.Object(shape, {
    additionalProperties: false,
  });
}
