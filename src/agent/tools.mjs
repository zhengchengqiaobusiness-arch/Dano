import { readFile } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { Type } from "@sinclair/typebox";
import { defineTool } from "@mariozechner/pi-coding-agent";
import { packageRoot, docDir, skillDir as skillPath } from "../paths.mjs";
import { appendEvidence, getEvidence, listEvidence, appendGoal, readGoal } from "../evidence/store.mjs";
import { runAction } from "../browser/actions.mjs";
import { persistBrowserSession } from "../browser/session.mjs";
import { takeSnapshot } from "../browser/snapshot.mjs";
import { getNetwork, listNetwork, requestKeyIndex } from "../browser/network.mjs";
import { readSkillFile, skillIdFor, writeSkillFile } from "../skillpack/files.mjs";
import { actionEvidenceSummary, filledLabels, finishSkill } from "../skillpack/verify.mjs";
import { GUIDE_NAMES, guideBody } from "./guides.mjs";
import { logLine } from "../log.mjs";

const GUIDE_FILES = {
  "skill-generator-auth-and-token.md": path.join(docDir(), "skill-generator-auth-and-token.md"),
  "skill-generator-live-options.md": path.join(docDir(), "skill-generator-live-options.md"),
  "skill-generator-ask-user-question-guide.md": path.join(docDir(), "skill-generator-ask-user-question-guide.md"),
  "writing-for-agents.md": path.join(packageRoot(), "skill", "writing-for-agents", "SKILL.md"),
  "writing-for-agents-mechanics.md": path.join(packageRoot(), "skill", "writing-for-agents", "SKILL-MECHANICS.md"),
};

const TOOL_SPECS = [
  { name: "browser_open", description: "打开入口同源的地址。", parameters: { type: "object", properties: { url: { type: "string" } }, required: ["url"] } },
  { name: "browser_snapshot", description: "读取当前页面快照。ref 整段照抄，形如 fN:eN@数字。", parameters: { type: "object", properties: {} } },
  { name: "browser_act", description: "ref 整段照抄最近一次 snapshot，格式 fN:eN@数字。@ 后数字对不上就是过期，用返回的新快照。同名不要请求程序挑第一个。快照开头的 goal_exact 是名字或列名出现在目标原文里的项，目标点名的控件点这些 ref。下拉分两次 click。返回里的 clicked、requests 和 snapshot 是这一下的结果。requests 为空就是这一下没有 xhr/fetch。返回 same_column 时那是这一列的格子。返回 uploaded 表示文件已经写入，采用 requests。返回 needs_upload 时只对同一 ref 调用 upload。填写用 fill。", parameters: { type: "object", properties: { action: { type: "string" }, ref: { type: "string" }, text: { type: "string" }, fields: { type: "array" }, key: { type: "string" }, file_path: { type: "string" } }, required: ["action"] } },
  { name: "browser_screenshot", description: "把当前画面作为图像送回。", parameters: { type: "object", properties: { ref: { type: "string" }, as_image: { type: "boolean" } } } },
  { name: "network_list", description: "列出 xhr/fetch 索引。", parameters: { type: "object", properties: { after_id: { type: "string" }, action_id: { type: "string" } } } },
  { name: "network_get", description: "按 id 读取一条请求的全文。body_missing 表示没有正文。", parameters: { type: "object", properties: { id: { type: "string" } }, required: ["id"] } },
  { name: "evidence_get", description: "按 id 读取一条证据全文。", parameters: { type: "object", properties: { id: { type: "string" } }, required: ["id"] } },
  { name: "read_page_asset", description: "读取本场已捕获、与入口同源的 javascript 响应。", parameters: { type: "object", properties: { url: { type: "string" } }, required: ["url"] } },
  { name: "read_guide", description: "写 Skill 前读取一份文档。name 见返回的 names。鉴权、提问、活选项和写作方法都从这里读，写进 SKILL.md、scripts/client.py、references/api.md。本场没有 CONTRACT.json 和 flow.py。", parameters: { type: "object", properties: { name: { type: "string" } }, required: ["name"] } },
  { name: "assist", description: "登录或验证码挡住时调用。这次调用会停住，直到人在预览里处理完并确认。返回里的 snapshot 才是确认后的当前页，从那张快照接着做。", parameters: { type: "object", properties: { reason: { type: "string" } }, required: ["reason"] } },
  { name: "append_goal", description: "只填充目标里仍为空的键，写入原句。", parameters: { type: "object", properties: { key: { type: "string" }, text: { type: "string" } }, required: ["key", "text"] } },
  { name: "write_skill_file", description: "只写 SKILL.md、scripts/client.py、references/api.md。", parameters: { type: "object", properties: { relative_path: { type: "string" }, contents: { type: "string" } }, required: ["relative_path", "contents"] } },
  { name: "read_skill_file", description: "读取已写的 Skill 文件。", parameters: { type: "object", properties: { relative_path: { type: "string" } }, required: ["relative_path"] } },
  { name: "run_skill_command", description: "在 Skill 目录执行 python scripts/client.py。argv 是字符串数组，例如 [\"python\", \"scripts/client.py\", \"list\"]。", parameters: { type: "object", properties: { argv: { type: "array", items: { type: "string" } } }, required: ["argv"] } },
  { name: "verify_skill", description: "校验三份文件是否引用了证据里的 path，以及这些 path 的键是否写在引用它们的函数里。返回 requests 和 filled。", parameters: { type: "object", properties: {} } },
];

function parseStructured(value) {
  if (typeof value !== "string") return value;
  const text = value.trim();
  if (!text.startsWith("{") && !text.startsWith("[")) return value;
  try {
    return JSON.parse(text);
  } catch {
    return value;
  }
}

function coerceArgs(args, schema) {
  const next = { ...(args || {}) };
  for (const [key, spec] of Object.entries(schema?.properties || {})) {
    if (!(key in next)) continue;
    if (spec.type === "object" || spec.type === "array") next[key] = parseStructured(next[key]);
  }
  return next;
}

function toolText(payload) {
  return { content: [{ type: "text", text: JSON.stringify(payload) }], details: payload };
}

export function toolNames() {
  return TOOL_SPECS.map((spec) => spec.name);
}

export function hostTools(recording) {
  const id = recording.id;
  const skillId = () => recording.skillId || skillIdFor(recording.subsystem, id);
  const tools = {
    async browser_open(args) {
      const result = await runAction(id, { action: "open", url: args.url });
      await appendEvidence(id, { kind: "action", summary: "open", body: result, body_missing: false });
      return result;
    },
    async browser_snapshot() {
      const snapshot = await takeSnapshot(id);
      const saved = await appendEvidence(id, { kind: "snapshot", summary: "snapshot", body: snapshot, body_missing: false });
      return { ...snapshot, evidence_id: saved.id };
    },
    async browser_act(args) {
      const fields = Array.isArray(args.fields) ? args.fields : [];
      if (args.action === "fill_fields") {
        for (const field of fields) {
          if (!field || typeof field.ref !== "string" || typeof field.text !== "string") {
            return { ok: false, error: "value_not_applied" };
          }
        }
      }
      const result = await runAction(id, args);
      const summary = actionEvidenceSummary(args.action, result);
      await appendEvidence(id, { kind: "action", summary, body: result, body_missing: false });
      return result;
    },
    async browser_screenshot(args) {
      const result = await runAction(id, { action: "screenshot", ref: args.ref });
      if (result.ok) {
        await appendEvidence(id, { kind: "screenshot", summary: "screenshot", body_missing: false, body: { mimeType: result.mimeType } });
      }
      return { ...result, as_image: true };
    },
    async network_list(args) {
      return { items: listNetwork(id, args || {}) };
    },
    async network_get(args) {
      const item = getNetwork(id, args.id);
      if (!item) return { ok: false, error: "missing" };
      return item;
    },
    async evidence_get(args) {
      return getEvidence(id, args.id);
    },
    async read_page_asset(args) {
      const origin = new URL(recording.startUrl).origin;
      const target = String(args.url || "");
      if (!target.startsWith(origin)) return { ok: false, error: "origin_denied" };
      const rows = listNetwork(id, {});
      const full = rows.map((row) => getNetwork(id, row.id)).find((row) => row && row.url === target && /javascript/i.test(row.content_type || ""));
      if (!full || full.body_missing) return { ok: false, body_missing: true };
      const saved = await appendEvidence(id, { kind: "asset", summary: target, body: full.response_body, body_missing: false });
      return { ok: true, evidence_id: saved.id, body: full.response_body };
    },
    async read_guide(args) {
      const name = path.basename(String(args.name || ""));
      if (!GUIDE_FILES[name]) return { ok: false, error: "unknown_guide", names: GUIDE_NAMES };
      const text = guideBody(name) || await readFile(GUIDE_FILES[name], "utf8");
      const saved = await appendEvidence(id, { kind: "guide", summary: name, body: name, body_missing: false });
      const scope = "成品只有 SKILL.md、scripts/client.py、references/api.md。鉴权、提问和活选项写进这三份。";
      return { ok: true, name, evidence_id: saved.id, text: `${scope}\n\n${text}` };
    },
    async assist(args) {
      recording.paused = true;
      recording.status = "waiting_operator";
      recording.assistReason = String(args.reason || "");
      recording.humanCanClick = true;
      recording.emit?.();
      const note = await new Promise((resolve) => {
        recording.releaseAssist = resolve;
      });
      recording.releaseAssist = null;
      if (recording.finished) return { ok: false, stopped: true };
      recording.paused = false;
      recording.status = "recording";
      recording.assistReason = "";
      recording.emit?.();
      const snapshot = await tools.browser_snapshot();
      await persistBrowserSession(id);
      return { ok: true, continued: true, note: String(note || ""), snapshot };
    },
    async append_goal(args) {
      return appendGoal(id, args.key, args.text);
    },
    async write_skill_file(args) {
      recording.skillId = skillId();
      return writeSkillFile(id, recording.skillId, args.relative_path, args.contents);
    },
    async read_skill_file(args) {
      return readSkillFile(skillId(), args.relative_path);
    },
    async run_skill_command(args) {
      const argv = (Array.isArray(args.argv) ? args.argv : String(args.argv || "").split(/\s+/)).map((item) => (
        typeof item === "string" ? item : String(item?.text || item?.value || item?.arg || "")
      )).filter(Boolean);
      if (!["python", "python3"].includes(argv[0]) || argv[1] !== "scripts/client.py") {
        return { ok: false, error: "command_rejected", argv: ["python", "scripts/client.py"] };
      }
      const cwd = skillPath(skillId());
      const result = await new Promise((resolve) => {
        const child = spawn(argv[0], argv.slice(1), {
          cwd,
          env: { ...process.env, CABP_AUTH_FILE: path.join(cwd, "config", "auth.local.json") },
          shell: false,
        });
        let stdout = "";
        let stderr = "";
        const timer = setTimeout(() => {
          child.kill();
          resolve({ code: 124, stdout, stderr: `${stderr}\ntimeout` });
        }, 30000);
        child.stdout.on("data", (chunk) => { stdout += chunk; });
        child.stderr.on("data", (chunk) => { stderr += chunk; });
        child.on("close", (code) => {
          clearTimeout(timer);
          resolve({ code: code ?? 1, stdout, stderr });
        });
        child.on("error", (error) => {
          clearTimeout(timer);
          resolve({ code: 127, stdout, stderr: error.message });
        });
      });
      const saved = await appendEvidence(id, {
        kind: "verify",
        ok: result.code === 0,
        argv,
        stdout: result.stdout,
        stderr: result.stderr,
        summary: argv.join(" "),
        body: result.stdout,
        body_missing: false,
      });
      return { ok: result.code === 0, evidence_id: saved.id, code: result.code, stdout: result.stdout, stderr: result.stderr };
    },
    async verify_skill() {
      recording.skillId = skillId();
      const verify = await finishSkill(recording);
      recording.verify = verify;
      recording.status = verify.status;
      recording.skillDir = skillPath(recording.skillId);
      recording.finished = verify.status === "skill_ready" || verify.status === "skill_written_needs_auth";
      recording.emit?.();
      return verify;
    },
    async context() {
      const goal = await readGoal(id).catch(() => ({}));
      const index = await listEvidence(id, { limit: 30 });
      const evidence = await listEvidence(id, { limit: 0 });
      const snaps = await listEvidence(id, { kinds: ["snapshot"], limit: 1 });
      let snapshot = null;
      if (snaps[0]) snapshot = await getEvidence(id, snaps[0].id);
      return { goal, snapshot, index, requests: requestKeyIndex(id), filled: filledLabels(evidence) };
    },
  };
  return tools;
}

export function wrapHostTools(host) {
  return TOOL_SPECS.filter((spec) => typeof host[spec.name] === "function").map((spec) => defineTool({
    name: spec.name,
    label: spec.name,
    description: spec.description,
    parameters: toTypeBox(spec.parameters),
    execute: async (_id, params) => {
      const args = coerceArgs(params || {}, spec.parameters);
      const started = Date.now();
      const argText = JSON.stringify(args).replace(/Bearer\s+[A-Za-z0-9._-]{8,}/g, "Bearer [redacted]").slice(0, 1200);
      logLine(`[cabp] 开始 ${spec.name} ${argText}`);
      const raw = await host[spec.name](spec.name === "browser_screenshot" ? { ...args, as_image: true } : args);
      const requests = Array.isArray(raw?.requests) ? raw.requests.map((row) => `${row.method || ""} ${row.path || ""} ${row.status || ""}`.trim()).join(" | ") : "";
      logLine(`[cabp] 结束 ${spec.name} ${Date.now() - started}ms ok=${raw?.ok !== false} error=${raw?.error || ""} clicked=${raw?.clicked || ""} uploaded=${Boolean(raw?.uploaded)} filled=${(raw?.filled || []).join("|")} requests=${requests}`);
      if (raw?.__image && raw.data && (args.as_image === true || raw.as_image === true)) {
        return {
          content: [
            { type: "text", text: JSON.stringify({ image_in_conversation: true }) },
            { type: "image", mimeType: raw.mimeType || "image/png", data: raw.data },
          ],
          details: { image_in_conversation: true },
        };
      }
      return toolText(raw);
    },
  }));
}

function toTypeBox(schema) {
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);
  const shape = {};
  for (const [key, value] of Object.entries(properties)) {
    let boxed;
    if (value.type === "integer") boxed = Type.Integer();
    else if (value.type === "boolean") boxed = Type.Boolean();
    else if (value.type === "object") boxed = Type.Object({}, { additionalProperties: true });
    else if (value.type === "array" && value.items?.type === "string") boxed = Type.Array(Type.String());
    else if (value.type === "array") boxed = Type.Array(Type.Object({}, { additionalProperties: true }));
    else boxed = Type.String();
    shape[key] = required.has(key) ? boxed : Type.Optional(boxed);
  }
  return Type.Object(shape, { additionalProperties: false });
}

export function promptFile() {
  return path.join(packageRoot(), "src", "agent", "prompt.md");
}
