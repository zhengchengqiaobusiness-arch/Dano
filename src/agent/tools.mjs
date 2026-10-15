import { access, readFile } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { Type } from "@sinclair/typebox";
import { defineTool } from "@mariozechner/pi-coding-agent";
import { packageRoot, docDir, skillDir as skillPath } from "../paths.mjs";
import { appendEvidence, getEvidence, listEvidence, appendGoal, readGoal } from "../evidence/store.mjs";
import { fieldFillText, runAction, withFillFieldText } from "../browser/actions.mjs";
import { browserSession, persistBrowserSession, withPage } from "../browser/session.mjs";
import { overlayControlFacts, overlayFactLists, overlayItemsLine, overlaySurfaceKind, takeSnapshot } from "../browser/snapshot.mjs";
import { compactNetworkIndex, foldedSummary, getNetwork, listNetwork, networkBodyForModel, requestKeyIndex } from "../browser/network.mjs";
import { applyVerifyToRecording, releaseVerifiedSkill } from "../session.mjs";
import { authSpawnEnv, readSkillFile, skillIdFor, writeRuntimeConfig, writeSkillFile } from "../skillpack/files.mjs";
import { actionEvidenceSummary, authStoppedOutput, clickedLabels, filledLabels, finishSkill, observationsForFile, structureErrorsForFile, verifySkill } from "../skillpack/verify.mjs";
import { redactSecrets } from "../auth-vault.mjs";
import { GUIDE_NAMES, guideBody } from "./guides.mjs";
import { logLine } from "../log.mjs";

const GUIDE_FILES = {
  "skill-generator-auth-and-token.md": path.join(docDir(), "skill-generator-auth-and-token.md"),
  "skill-generator-live-options.md": path.join(docDir(), "skill-generator-live-options.md"),
  "skill-generator-ask-user-question-guide.md": path.join(docDir(), "skill-generator-ask-user-question-guide.md"),
  "skill-generator-workflow.md": path.join(docDir(), "skill-generator-workflow.md"),
  "writing-for-agents.md": path.join(packageRoot(), "skill", "writing-for-agents", "SKILL.md"),
  "writing-for-agents-mechanics.md": path.join(packageRoot(), "skill", "writing-for-agents", "SKILL-MECHANICS.md"),
};

const TOOL_SPECS = [
  { name: "browser_open", description: "打开入口同源的地址。已在该地址则只返回当前树。同源换 hash/path 可以 goto。跨源拒绝。", parameters: { type: "object", properties: { url: { type: "string" } }, required: ["url"] } },
  { name: "browser_snapshot", description: "读取当前页面快照树。只点树里的 [ref=fN:eN@数字]。goal_exact / overlay_exact / row_facts 是查找表，不是点击清单。返回 overlay_fields 与 overlay_editable（当前层非只读可填，含 ref；没有弹层时是页面上的 textbox/combobox）。", parameters: { type: "object", properties: {} } },
  { name: "browser_act", description: "ref 整段照抄最近一次 snapshot 树里的 [ref=fN:eN@数字]。@ 后数字对不上就重新 snapshot，只点新树。同名不要挑第一个。没有 ref 就停。不要并行多次 browser_act。同一张树上要改多格，用一次 fill_fields。fill_fields 的 fields 每项是 ref 加要填的字（text 或 value）。填和选用树里 textbox/combobox 以及 overlay_editable 的 ref。返回 clicked、requests、snapshot、overlay_fields、overlay_editable。requests 空只说明这一下没有 xhr/fetch。empty/required/readonly 是事实；empty=true 是没值，required 才是当前层必填还空；empty=false 不要当没填。弹层开着时只点当前树里这层的 ref，不要点 goal_exact 里被挡住的项。in_overlay 表示点的是 overlay_exact 里的控件；layer_idle 表示这层动了但没有 xhr。needs_click 表示这一格不能直接填，先 click 同一个 ref 再填。behind_overlay 时先看 overlay_exact。还没采用请求就不要点会关掉这层的控件。page_unchanged 时换 ref。uploaded 表示文件已写入。needs_upload 时对同一 ref 调用 upload。", parameters: { type: "object", properties: { action: { type: "string" }, ref: { type: "string" }, text: { type: "string" }, fields: { type: "array" }, key: { type: "string" }, file_path: { type: "string" } }, required: ["action"] } },
  { name: "browser_screenshot", description: "把当前画面作为图像送回。不换树，点、填仍用最近一次 snapshot 的 [ref=]。", parameters: { type: "object", properties: { ref: { type: "string" }, as_image: { type: "boolean" } } } },
  { name: "network_list", description: "列出本场全部 xhr/fetch 索引（id/method/path/keys/empty/response_keys）。empty 是这次请求里的空键，不是永远空。写字段时看这份 listed，不要只看点击口袋。默认不列 HEAD、静态文件和无动作的重复请求，all=true 看全部。action_id 为空时 after_action/after_ms 是它在哪个动作之后多久发出，只是时间事实，自己判断是否由那一下引起。post_data_unreadable 表示浏览器没有交出带文件的表单正文；字段名看这次 upload 返回的 file_field，或用 read_page_asset 读前端脚本核对。", parameters: { type: "object", properties: { after_id: { type: "string" }, action_id: { type: "string" }, all: { type: "boolean" } } } },
  { name: "network_get", description: "按 id 读取一条请求。正文过大则省略并给出 response_keys，用 evidence_get 同 id 取全文。body_missing 表示没有正文。", parameters: { type: "object", properties: { id: { type: "string" } }, required: ["id"] } },
  { name: "evidence_get", description: "按 id 读取一条证据全文。", parameters: { type: "object", properties: { id: { type: "string" } }, required: ["id"] } },
  { name: "read_page_asset", description: "读取本场已捕获、与入口同源的 javascript 响应。", parameters: { type: "object", properties: { url: { type: "string" } }, required: ["url"] } },
  { name: "read_guide", description: "写 Skill 前读取文档。name 用返回的 names 里的一份，或 all 一次读完名单。鉴权、提问、活选项和写作方法都从这里读，写进 SKILL.md、scripts/client.py、references/api.md。本场没有 CONTRACT.json 和 flow.py。", parameters: { type: "object", properties: { name: { type: "string" } }, required: ["name"] } },
  { name: "assist", description: "当前页被登录框或验证码挡住时调用。run_skill_command 的 401 或打包凭证过期不要调用。这次调用会停住，直到人在预览里处理完并确认。返回里的 snapshot 才是确认后的当前页，从那张快照接着做。", parameters: { type: "object", properties: { reason: { type: "string" } }, required: ["reason"] } },
  { name: "append_goal", description: "只填充目标里仍为空的键，写入原句。", parameters: { type: "object", properties: { key: { type: "string" }, text: { type: "string" } }, required: ["key", "text"] } },
  { name: "write_skill_file", description: "只写 SKILL.md、scripts/client.py、references/api.md。SKILL.md 必须以 --- 包住 name 和 description。references/api.md 标题必须是字段、绑定、未解决、已验证读命令，缺了会拒写。写入成功时顺带返回这份文件现在过不了 verify_skill 的结构项 structure_errors，先改这份文件再跑命令。", parameters: { type: "object", properties: { relative_path: { type: "string" }, contents: { type: "string" } }, required: ["relative_path", "contents"] } },
  { name: "edit_skill_file", description: "改已写文件里的一段：old_text 必须在文件里恰好出现一次，替换成 new_text。只能改 SKILL.md、scripts/client.py、references/api.md。返回与 write_skill_file 相同（含 structure_errors）。只改出错的那一段，不要整份重写。", parameters: { type: "object", properties: { relative_path: { type: "string" }, old_text: { type: "string" }, new_text: { type: "string" } }, required: ["relative_path", "old_text", "new_text"] } },
  { name: "read_skill_file", description: "读取已写的 Skill 文件。", parameters: { type: "object", properties: { relative_path: { type: "string" } }, required: ["relative_path"] } },
  { name: "run_skill_command", description: "在 Skill 目录执行 python3 scripts/client.py。argv 是字符串数组，例如 [\"python3\", \"scripts/client.py\", \"list\"]。本机没有 python3 时运行时改用 python。先写出 scripts/client.py 再跑；文件不在会返回 missing_file。", parameters: { type: "object", properties: { argv: { type: "array", items: { type: "string" } } }, required: ["argv"] } },
  { name: "verify_skill", description: "校验三文件结构：文件在、可调用、SKILL.md 里出现的读命令（含表格单元格）已跑、引用 path 在证据里、引用 path 的函数写了该请求的键。不判断字段该谁填、必填与否或来源，不要求只写目标发明 GET。返回 listed 用的 requests 和 filled。observations 是页面、字段、问句之间的对照，不阻止产出。逐条判断：确实漏了或写错了，就用 edit_skill_file 改了再 verify_skill；没漏就不用改。", parameters: { type: "object", properties: {} } },
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
  return { content: [{ type: "text", text: redactSecrets(JSON.stringify(payload)) }], details: payload };
}

export function packedAuthAssist(reason) {
  return /auth\.local\.json|headers\.Authorization|DANO_AUTH|从请求头复制|vault|把 token 写/i.test(String(reason || ""));
}

export function sessionAssistBlocked(reason) {
  return /\bno_browser\b|origin_denied|会话已断开|没有浏览器/i.test(String(reason || ""));
}

export function pythonFallback(bin) {
  return bin === "python3" ? "python" : bin === "python" ? "python3" : "";
}

export async function spawnClientWithPythonFallback(argv, cwd, authFile, recording, run = spawnClient) {
  let result = await run(argv, cwd, authFile, recording);
  if (result.code === 127 || result.code === 9009) {
    const alt = pythonFallback(argv[0]);
    if (alt) result = await run([alt, ...argv.slice(1)], cwd, authFile, recording);
  }
  return result;
}

function clientSpawnEnv(recording, authFile) {
  const env = {
    ...process.env,
    CABP_AUTH_FILE: authFile,
    DANO_AUTH_FILE: authFile,
  };
  if (!env.DANO_AUTH_HEADERS) Object.assign(env, authSpawnEnv(authFile));
  if (!env.DANO_BASE_URL) {
    try {
      env.DANO_BASE_URL = new URL(recording?.startUrl || "").origin;
    } catch {
      // 录制入口解析不了就留给脚本读 runtime.json
    }
  }
  return env;
}

function spawnClient(argv, cwd, authFile, recording) {
  return new Promise((resolve) => {
    const child = spawn(argv[0], argv.slice(1), {
      cwd,
      env: clientSpawnEnv(recording, authFile),
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
      if (!browserSession(id)) return { ok: false, error: "no_browser", epoch: 0, refs: [], text: "" };
      const taken = await withPage(id, async () => {
        let snapshot = await takeSnapshot(id);
        if (snapshot?.ok === false) return { snapshot };
        if (!(snapshot?.refs || []).length) {
          await new Promise((resolve) => setTimeout(resolve, 800));
          snapshot = await takeSnapshot(id);
          if (snapshot?.ok === false) return { snapshot };
        }
        let overlay = {};
        try {
          overlay = overlayFactLists(await overlayControlFacts(id));
        } catch {
          overlay = {};
        }
        return { snapshot, overlay };
      });
      if (taken.snapshot?.ok === false) return taken.snapshot;
      const { snapshot, overlay } = taken;
      const saved = await appendEvidence(id, {
        kind: "snapshot",
        summary: "snapshot",
        body: { ...snapshot, ...overlay, overlay_surface: overlaySurfaceKind(snapshot.text) },
        body_missing: false,
      });
      return { ...snapshot, ...overlay, overlay_surface: overlaySurfaceKind(snapshot.text), evidence_id: saved.id };
    },
    async browser_act(args) {
      const act = withFillFieldText(args);
      const fields = Array.isArray(act.fields) ? act.fields : [];
      if (act.action === "fill_fields") {
        for (const field of fields) {
          if (!field || typeof field.ref !== "string" || fieldFillText(field) == null) {
            return { ok: false, error: "value_not_applied", hint: "fill_fields 每项需要字符串 ref，以及 text 或 value" };
          }
        }
      }
      const result = await runAction(id, act);
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
      return { items: listNetwork(id, args || {}), folded: foldedSummary(id), hint: "folded 里是 HEAD、静态文件和无动作的重复请求；需要时 all=true 看全部" };
    },
    async network_get(args) {
      const item = getNetwork(id, args.id);
      if (!item) return { ok: false, error: "missing" };
      return networkBodyForModel(item);
    },
    async evidence_get(args) {
      try {
        return await getEvidence(id, args.id);
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
        const item = getNetwork(id, args.id);
        if (item) return item;
        return { ok: false, error: "missing", hint: "这个 id 没有全文。listed 里的请求用 network_get。" };
      }
    },
    async read_page_asset(args) {
      const origin = new URL(recording.startUrl).origin;
      const target = String(args.url || "");
      if (!target.startsWith(origin)) return { ok: false, error: "origin_denied" };
      const rows = listNetwork(id, { all: true });
      const full = rows.map((row) => getNetwork(id, row.id)).find((row) => row && row.url === target && /javascript/i.test(row.content_type || ""));
      if (!full || full.body_missing) return { ok: false, body_missing: true };
      const saved = await appendEvidence(id, { kind: "asset", summary: target, body: full.response_body, body_missing: false });
      return { ok: true, evidence_id: saved.id, body: full.response_body };
    },
    async read_guide(args) {
      const name = path.basename(String(args.name || ""));
      const scope = "成品只有 SKILL.md、scripts/client.py、references/api.md。鉴权、提问和活选项写进这三份。";
      if (!name || name === "index" || name === "names" || name === "list") {
        return { ok: true, names: GUIDE_NAMES, hint: "name 用这些值之一，或 all 一次读完" };
      }
      if (name === "all") {
        const texts = [];
        const evidence_ids = [];
        for (const item of GUIDE_NAMES) {
          const body = guideBody(item) || await readFile(GUIDE_FILES[item], "utf8");
          const saved = await appendEvidence(id, { kind: "guide", summary: item, body: item, body_missing: false });
          evidence_ids.push(saved.id);
          texts.push(`# ${item}\n${body}`);
        }
        return { ok: true, name: "all", names: GUIDE_NAMES, evidence_ids, text: `${scope}\n\n${texts.join("\n\n")}` };
      }
      if (!GUIDE_FILES[name]) return { ok: false, error: "unknown_guide", names: GUIDE_NAMES };
      const text = guideBody(name) || await readFile(GUIDE_FILES[name], "utf8");
      const saved = await appendEvidence(id, { kind: "guide", summary: name, body: name, body_missing: false });
      return { ok: true, name, evidence_id: saved.id, text: `${scope}\n\n${text}` };
    },
    async assist(args) {
      if (packedAuthAssist(args.reason)) {
        recording.skillId = skillId();
        await persistBrowserSession(id).catch(() => null);
        await writeRuntimeConfig(recording, recording.skillId);
        const snapshot = await tools.browser_snapshot();
        return {
          ok: false,
          error: "assist_not_for_packed_auth",
          hint: "打包凭证过期不要 assist，不要把凭证写进对话。继续写三文件；读命令 401 标 auth_expired。",
          snapshot,
        };
      }
      if (sessionAssistBlocked(args.reason)) {
        const snapshot = await tools.browser_snapshot().catch(() => ({ text: "", refs: [] }));
        return {
          ok: false,
          error: "assist_not_for_session",
          hint: "assist 只用于当前页的登录框或验证码。no_browser 或 origin_denied 时重新 snapshot，不要 assist。",
          snapshot,
        };
      }
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
      await writeRuntimeConfig(recording, recording.skillId);
      const result = await writeSkillFile(id, recording.skillId, args.relative_path, args.contents);
      if (result?.ok) {
        recording.wroteSkill = true;
        recording.emit?.();
        // 同一套结构校验在写入时就摆回来，只给这份文件的项，省掉一轮 verify_skill 往返
        try {
          const verify = await verifySkill(skillPath(recording.skillId), id, recording, { dryRun: true });
          const rel = String(args.relative_path || "").replace(/\\/g, "/");
          const structure_errors = structureErrorsForFile(verify.errors, rel);
          const observations = observationsForFile(verify.observations, rel);
          const alignment = verify.alignment || null;
          const extra = {
            ...(structure_errors.length ? { structure_errors } : {}),
            ...(observations.length ? { observations } : {}),
            ...(alignment ? { alignment } : {}),
          };
          if (structure_errors.length) {
            return { ...result, ...extra, hint: "已写入。对照 overlay_editable_names、parsed_page_names、question_ids 改这份文件再跑命令。" };
          }
          if (Object.keys(extra).length) return { ...result, ...extra };
        } catch {
          // 写入已成功；结构预检失败就留给 verify_skill
        }
      }
      return result;
    },
    async edit_skill_file(args) {
      recording.skillId = skillId();
      let current;
      try {
        current = await readSkillFile(recording.skillId, args.relative_path);
      } catch {
        return { ok: false, error: "missing_file", path: String(args.relative_path || "") };
      }
      if (!current?.ok) return current;
      const oldText = String(args.old_text ?? "");
      if (!oldText) return { ok: false, error: "old_text_required" };
      const count = current.contents.split(oldText).length - 1;
      if (count === 0) return { ok: false, error: "old_text_not_found", hint: "old_text 从 read_skill_file 的原文整段照抄" };
      if (count > 1) return { ok: false, error: "old_text_not_unique", count, hint: "多带几行上下文，让 old_text 只出现一次" };
      const contents = current.contents.replace(oldText, () => String(args.new_text ?? ""));
      return tools.write_skill_file({ relative_path: args.relative_path, contents });
    },
    async read_skill_file(args) {
      return readSkillFile(skillId(), args.relative_path);
    },
    async run_skill_command(args) {
      const argv = (Array.isArray(args.argv) ? args.argv : String(args.argv || "").split(/\s+/)).map((item) => (
        typeof item === "string" ? item : String(item?.text || item?.value || item?.arg || "")
      )).filter(Boolean);
      if (!["python", "python3"].includes(argv[0]) || argv[1] !== "scripts/client.py") {
        return { ok: false, error: "command_rejected", argv: ["python3", "scripts/client.py"] };
      }
      recording.skillId = skillId();
      await writeRuntimeConfig(recording, recording.skillId);
      const cwd = skillPath(recording.skillId);
      try {
        await access(path.join(cwd, "scripts", "client.py"));
      } catch {
        return { ok: false, error: "missing_file", path: "scripts/client.py" };
      }
      const authFile = path.join(cwd, "config", "auth.local.json");
      let result = await spawnClientWithPythonFallback(argv, cwd, authFile, recording);
      if (result.code !== 0 && authStoppedOutput(`${result.stdout}\n${result.stderr}`)) {
        await persistBrowserSession(id).catch(() => null);
        await writeRuntimeConfig(recording, recording.skillId);
        result = await spawnClient(argv, cwd, authFile, recording);
        result.repacked = true;
      }
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
      if (result.code !== 0 && authStoppedOutput(`${result.stdout}\n${result.stderr}`)) {
        return {
          ok: false,
          error: "auth_expired",
          hint: "打包凭证过期。不要 assist，不要把凭证写进对话。仍要写完 SKILL.md 和 references/api.md，再 verify_skill。401 不是产出失败。",
          evidence_id: saved.id,
          code: result.code,
          stdout: redactSecrets(result.stdout),
          stderr: redactSecrets(result.stderr),
        };
      }
      if (result.code !== 0 && /缺少 credential|credential 对象|没有 credential/i.test(`${result.stdout}\n${result.stderr}`)) {
        return {
          ok: false,
          error: "not_invocable",
          hint: "业务请求把 config/auth.local.json 的 headers 整袋带上。有任意鉴权头就发，不要只认某一个键名。credential 只有 url 和 method 时才存在，不要当成必有对象。",
          evidence_id: saved.id,
          code: result.code,
          stdout: redactSecrets(result.stdout),
          stderr: redactSecrets(result.stderr),
        };
      }
      return { ok: result.code === 0, evidence_id: saved.id, code: result.code, stdout: result.stdout, stderr: result.stderr };
    },
    async verify_skill() {
      recording.skillId = skillId();
      const verify = await finishSkill(recording);
      applyVerifyToRecording(recording, verify, skillPath(recording.skillId));
      if (verify?.status === "skill_ready" || verify?.status === "skill_written_needs_auth") {
        const exported = await releaseVerifiedSkill(recording);
        if (exported) verify.export = exported;
      }
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
      let overlay_fields = [];
      try {
        overlay_fields = await withPage(id, () => overlayControlFacts(id));
      } catch {
        overlay_fields = [];
      }
      const snapshotText = snapshot?.body?.text || snapshot?.text || "";
      return {
        goal,
        snapshot,
        index,
        listed: compactNetworkIndex(listNetwork(id)),
        requests: requestKeyIndex(id),
        clicked: clickedLabels(evidence),
        filled: filledLabels(evidence),
        seen_goal_names: browserSession(id)?.seenGoalNames || [],
        overlay_surface: overlaySurfaceKind(snapshotText),
        ...overlayFactLists(overlay_fields),
      };
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
      const requiredEmpty = overlayItemsLine(raw?.required_empty_overlay) ? ` required_empty=${overlayItemsLine(raw.required_empty_overlay)}` : "";
      const empty = Array.isArray(raw?.empty_overlay) && raw.empty_overlay.length ? ` empty=${raw.empty_overlay.join("|")}` : "";
      const editable = Array.isArray(raw?.overlay_editable) && raw.overlay_editable.length ? ` editable=${raw.overlay_editable.map((row) => row.name || row.role).join("|")}` : "";
      logLine(`[cabp] 结束 ${spec.name} ${Date.now() - started}ms ok=${raw?.ok !== false} error=${raw?.error || ""} clicked=${raw?.clicked || ""} uploaded=${Boolean(raw?.uploaded)} filled=${(raw?.filled || []).join("|")}${requiredEmpty}${empty}${editable} requests=${requests}`);
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
  return Type.Object(shape, { additionalProperties: true });
}

export function promptFile() {
  return path.join(packageRoot(), "src", "agent", "prompt.md");
}
