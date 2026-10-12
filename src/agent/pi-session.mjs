import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { dataRoot, packageRoot, recordingDir } from "../paths.mjs";
import { readGoal } from "../evidence/store.mjs";
import { redactSecrets } from "../auth-vault.mjs";
import { applyPiModelConfig } from "./pi-model.mjs";
import { installOpenAIToolCallStreamCompatibility } from "./openai-stream-compat.mjs";
import { hostTools, toolNames, wrapHostTools } from "./tools.mjs";
import { logLine } from "../log.mjs";

function toolLine(toolName, args) {
  const action = args?.action ? ` ${args.action}` : "";
  const ref = args?.ref ? ` ${args.ref}` : "";
  return `${toolName}${action}${ref}`;
}

export function resultLine(result) {
  const text = redactSecrets(result?.content?.find?.((item) => item?.type === "text")?.text || "");
  if (!text) return result?.isError ? "失败" : "完成";
  try {
    const body = JSON.parse(text);
    const errors = Array.isArray(body?.errors) ? body.errors : [];
    const authOnly = errors.length > 0 && errors.every((item) => item.code === "auth_expired");
    if (authOnly && (body.ok === true || body.status === "skill_written_needs_auth" || body.status === "skill_ready")) {
      return "完成 结构已过 鉴权过期";
    }
    if (body?.error === "auth_expired") {
      return "完成 鉴权过期 继续写三文件";
    }
    if (errors.length) {
      const codes = errors.map((item) => {
        const parts = [item.code, item.name, item.field, item.path, item.file, item.key, item.command, item.heading].filter(Boolean);
        const hint = String(item.hint || "").replace(/\s+/g, " ").trim();
        if (hint) parts.push(hint.slice(0, 60));
        return parts.join(":");
      }).slice(0, 6).join(" ");
      return `失败 ${codes}`.slice(0, 500);
    }
    if (body?.ok === false) {
      const extra = [body.error, body.hint, body.file, body.stderr, body.stdout, ...(body.names || []), ...(body.writable || [])].filter(Boolean).join(" ");
      return `失败 ${extra}`.slice(0, 500);
    }
    const requiredEmpty = Array.isArray(body.required_empty_overlay) && body.required_empty_overlay.length
      ? ` 必填空=${body.required_empty_overlay.slice(0, 8).join("|")}`
      : "";
    const empty = Array.isArray(body.empty_overlay) && body.empty_overlay.length
      ? ` 空=${body.empty_overlay.slice(0, 8).join("|")}`
      : "";
    if (body?.ok === true && body.clicked) {
      const quiet = Array.isArray(body.requests) && body.requests.length === 0 ? " 无请求" : "";
      const layer = body.layer_idle ? " 层无请求" : "";
      return `完成 ${body.clicked}${quiet}${layer}${requiredEmpty}${empty}`;
    }
    if (body?.ok === true && (requiredEmpty || empty)) return `完成${requiredEmpty}${empty}`;
    if (body?.ok === true) return "完成";
    if (Array.isArray(body?.refs)) return `完成 refs=${body.refs.length}`;
    if (body?.image_in_conversation) return "完成 图像";
  } catch {
    // 不是 JSON 就只留一行
  }
  return text.split("\n")[0].slice(0, 180);
}

const PAGE_TOOLS = new Set(["browser_open", "browser_snapshot", "browser_act", "browser_screenshot"]);
const SKILL_TOOLS = new Set(["write_skill_file", "run_skill_command", "verify_skill"]);
const MAX_SKILL_CONTINUES = 8;

export function listedWriteKeys(listed) {
  const keys = [];
  const seen = new Set();
  for (const row of Array.isArray(listed) ? listed : []) {
    if (!/POST|PUT|PATCH|DELETE/i.test(String(row.method || ""))) continue;
    for (const key of Array.isArray(row.keys) ? row.keys : []) {
      const name = String(key || "").trim();
      if (!name || seen.has(name)) continue;
      seen.add(name);
      keys.push(name);
    }
  }
  return keys;
}

function quotedLookupNames(text, heading) {
  const names = [];
  let take = false;
  for (const line of String(text || "").split("\n")) {
    const trimmed = line.trim();
    if (trimmed === heading) {
      take = true;
      continue;
    }
    if (take && /^(goal_exact|overlay_exact|row_facts|frame |epoch )/.test(trimmed)) break;
    if (!take) continue;
    for (const match of line.matchAll(/"((?:\\.|[^"])*)"/g)) {
      const name = match[1].replace(/\\"/g, '"').trim();
      if (name && !names.includes(name)) names.push(name);
    }
  }
  return names;
}

export function unmatchedGoalNames(goalNamed, clicked, goalText = "") {
  const labels = (clicked || []).map((item) => String(item || ""));
  const goal = String(goalText || "");
  const names = [];
  for (const raw of goalNamed || []) {
    const name = String(raw || "").trim();
    if (name.length < 2 || /^\d+(\.\d+)?%?$/.test(name)) continue;
    if (goal && !goal.includes(name)) continue;
    if (labels.some((label) => label.includes(name))) continue;
    if (!names.includes(name)) names.push(name);
  }
  return names;
}

export function recordingStayOnPage({ verifyErrors, pageProgressed, progressed, unmatchedGoalNames, requiredEmptyOverlay }) {
  if (Array.isArray(verifyErrors) && verifyErrors.length) return false;
  if (pageProgressed && !progressed) return true;
  if (Array.isArray(unmatchedGoalNames) && unmatchedGoalNames.length > 0) return true;
  return Array.isArray(requiredEmptyOverlay) && requiredEmptyOverlay.length > 0;
}

export function factsForContinue(context) {
  const snapshot = context?.snapshot;
  const text = snapshot?.body?.text || snapshot?.text || "";
  const overlay_fields = Array.isArray(context?.overlay_fields) ? context.overlay_fields.slice(0, 160) : [];
  const overlay_editable = Array.isArray(context?.overlay_editable)
    ? context.overlay_editable.slice(0, 160)
    : overlay_fields.filter((row) => !row.readonly);
  const fromSnap = quotedLookupNames(text, "goal_exact");
  const seen = Array.isArray(context?.seen_goal_names) ? context.seen_goal_names : [];
  const goal_named = [];
  for (const name of [...seen, ...fromSnap]) {
    if (name && !goal_named.includes(name)) goal_named.push(name);
  }
  const goalText = `${context?.goal?.goal_text || ""}`;
  const clicked = context?.clicked || [];
  return {
    goal: context?.goal || {},
    listed: context?.listed || [],
    listed_write_keys: Array.isArray(context?.listed_write_keys)
      ? context.listed_write_keys.slice(0, 80)
      : listedWriteKeys(context?.listed).slice(0, 80),
    clicked,
    filled: context?.filled || [],
    goal_named: goal_named.slice(0, 40),
    goal_named_unclicked: unmatchedGoalNames(goal_named, clicked, goalText).slice(0, 40),
    overlay_fields,
    overlay_editable: overlay_editable.map((row) => ({
      ref: row.ref || "",
      name: row.name || "",
      column: row.column || "",
      role: row.role || "",
      input: row.input || "",
      empty: Boolean(row.empty),
      required: Boolean(row.required),
    })),
    empty_overlay: Array.isArray(context?.empty_overlay) ? context.empty_overlay.slice(0, 40) : [],
    required_empty_overlay: Array.isArray(context?.required_empty_overlay) ? context.required_empty_overlay.slice(0, 40) : [],
    snapshot: String(text).slice(0, 12000),
  };
}

let methodPack = "";
const METHOD_CLOSER = "Playwright 只说明怎么点当前树。derive-client 只说明 scripts/client.py 怎么发 HTTP。成品仍是三文件。references/api.md 标题只能是字段、绑定、未解决、已验证读命令，不要用接口 path 当标题。调用方读 SKILL.md 的 YAML 与 questions[]。";

async function loadMethodPack() {
  if (methodPack) return methodPack;
  const [promptText, playwright, derive] = await Promise.all([
    readFile(path.join(packageRoot(), "src", "agent", "prompt.md"), "utf8"),
    readFile(path.join(packageRoot(), "skill", "playwright-cli", "SKILL.md"), "utf8"),
    readFile(path.join(packageRoot(), "skill", "derive-client", "SKILL.md"), "utf8"),
  ]);
  methodPack = [promptText, METHOD_CLOSER, playwright, derive].join("\n\n");
  return methodPack;
}

export async function recordingSystemPrompt(goal) {
  return `${await loadMethodPack()}\n\n${METHOD_CLOSER}\n\n${JSON.stringify(goal)}`;
}

export function nextRecordingPrompt({ finished, paused, progressed, continues, verifyErrors, pageProgressed, unmatchedGoalNames, requiredEmptyOverlay }) {
  if (finished || paused) return null;
  const errors = Array.isArray(verifyErrors) && verifyErrors.length ? `\n${JSON.stringify(verifyErrors)}` : "";
  if (Array.isArray(verifyErrors) && verifyErrors.length) {
    if (continues >= MAX_SKILL_CONTINUES) return null;
    if (verifyErrors.every((item) => item.code === "auth_expired")) {
      return `三文件已写出。打包凭证过期，不要 assist，不要改脚本猜 token，不要把凭证写进对话。结构校验已通过。${errors}`;
    }
    return `校验失败。对照 listed、overlay_editable、listed_write_keys 只改 SKILL.md、scripts/client.py、references/api.md，再 run_skill_command。不要 browser_act。references/api.md 标题只能是字段、绑定、未解决、已验证读命令，不要用接口 path 当标题。字段段每条要有 page_name 和 source（七个词之一，说明不要写进 source）。调用方字段要有 questions[]，页面下拉要有 options。${errors}`;
  }
  if (recordingStayOnPage({ verifyErrors, pageProgressed, progressed, unmatchedGoalNames, requiredEmptyOverlay })) {
    return "页面目标还没做完。对照当前树、goal_named、goal_named_unclicked、clicked、row_facts、overlay_editable、required_empty_overlay、listed_write_keys 和目标原文接着点、填。目标原文点名的名字或列，在当前树找 [ref=] 去点；goal_named_unclicked 里还有名字、或 required_empty_overlay 还非空时不要当成做完，也不要 write_skill_file 或 verify_skill。指针样式不是能不能点的依据。empty=true 的还要填。stale_ref 或 behind_overlay 只用工具返回的新树，不要 browser_open。人可以在同一个预览里点；actor=human 时等这次操作进当前树和 listed 再接着做。";
  }
  if (continues >= MAX_SKILL_CONTINUES) return null;
  const lead = progressed
    ? "页面操作的总结不是结束。对照 listed（本场全部 xhr/fetch 索引）、overlay_editable、listed_write_keys 和目标原文写三文件。SKILL.md 必须含可原样复制的 title+\"questions\":[]；问句覆盖 overlay_editable 的每一项和 listed_write_keys 里来源为 caller 的键；页面 combobox/listbox/radio 在调用方也是 options，不要自由文本，不要 dataSource。写：调用方提交后再 confirm+formIds，python3 加 --confirm。嵌套数组保持为行，不要压成一个字符串。listed 某键这次 empty/[] 只说明这次传了空，同场另有选项请求则再读，不要冻成空数组。手册必填必须和 argparse 一致。每个 add_parser 必须 set_defaults(func=)。写入子命令必须有 --confirm。基地址只读 DANO_BASE_URL 或 runtime.json。SKILL.md 里出现的每一条读命令（含表格）都要跑。正文过大时 network_get 会省略，用 evidence_get 同 id。成品必须可调用。目标只要求写入时不要发明 GET。filled 里的控件名不是调用方必填清单。"
    : "Skill 还没产出。";
  return `${lead}read_guide 读完名单。对照 listed（本场全部 xhr/fetch 索引）写字段。要采用的请求打开全文。来源标签和命令行谁填必须一致。overlay_editable 上的控件是调用方填或选，不要改成系统自动。empty 里的键传空。只有 auth.local.json 的 credential 同时有 url 和 method 时才重放；listed 里的刷新请求不是必须重放。业务请求把 headers 整袋带上，有任意鉴权头就发，不要只认某一个键名。读命令 401 不要 assist、不要猜 token，仍要写完 SKILL.md 和 references/api.md，再 verify_skill。401 是打包凭证过期，不是产出失败。DANO_AUTH_HEADERS 只覆盖同名头。scripts/client.py 只用 Python 标准库。跑读命令（若目标含读），再 verify_skill。${errors}`;
}

let startOverride = null;

export function setStartRecordingPi(fn) {
  startOverride = fn;
}

export async function startRecordingPi({ recordingId, tools, recording }) {
  if (startOverride) return startOverride({ recordingId, tools, recording });
  const host = tools || hostTools(recording);
  const {
    createAgentSession, SessionManager, AuthStorage, ModelRegistry, DefaultResourceLoader,
  } = await import("@mariozechner/pi-coding-agent");
  const agentDir = path.join(dataRoot(), "pi-agent");
  const cwd = path.join(recordingDir(recordingId), "pi-cwd");
  await mkdir(agentDir, { recursive: true });
  await mkdir(cwd, { recursive: true });
  const authStorage = AuthStorage.create(path.join(agentDir, "auth.json"));
  const modelRegistry = ModelRegistry.create(authStorage, path.join(agentDir, "models.json"));
  const resolved = applyPiModelConfig(authStorage, modelRegistry);
  installOpenAIToolCallStreamCompatibility({ baseUrl: resolved.baseUrl });
  const goal = await readGoal(recordingId);
  const systemPrompt = await recordingSystemPrompt(goal);
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir,
    systemPromptOverride: () => systemPrompt,
  });
  const customTools = wrapHostTools(host);
  const created = await createAgentSession({
    cwd,
    agentDir,
    authStorage,
    modelRegistry,
    model: resolved.model,
    noTools: "builtin",
    tools: toolNames(),
    customTools,
    resourceLoader,
    sessionManager: SessionManager.inMemory(),
  });
  const session = created.session;
  let lastTool = "";
  let progressed = false;
  let pageProgressed = false;
  let continues = 0;
  session.subscribe?.((event) => {
    const type = String(event?.type || "");
    const assistant = event?.assistantMessageEvent;
    if (type === "tool_execution_start") {
      if (SKILL_TOOLS.has(event.toolName)) progressed = true;
      if (PAGE_TOOLS.has(event.toolName)) pageProgressed = true;
      lastTool = toolLine(event.toolName, event.args);
      const line = `调用 ${lastTool}`;
      logLine(`[pi] ${line}`);
      recording?.emitThought?.({ kind: "tool", phase: "start", text: line });
    }
    if (type === "tool_execution_end") {
      const line = `${lastTool || event.toolName || "tool"} ${resultLine(event.result)}`;
      logLine(`[pi] ${line}`);
      recording?.emitThought?.({
        kind: "tool",
        phase: "end",
        ok: !event.isError,
        text: line,
      });
    }
  });
  let prompting = null;
  const queued = [];
  async function prompt(text) {
    if (recording?.finished) return;
    if (recording?.paused || prompting) {
      queued.push(text);
      return prompting;
    }
    try {
      prompting = session.prompt(text);
      await prompting;
    } catch (error) {
      const message = String(error?.message || error || "");
      if (!session.isStreaming) {
        logLine(`[pi] 模型中断 ${message.slice(0, 200)}`);
        recording?.emitThought?.({ kind: "tool", phase: "end", ok: false, text: `模型中断 ${message.slice(0, 200)}` });
        return;
      }
      await session.steer(text);
    } finally {
      prompting = null;
      if (recording?.finished) return;
      const next = queued.shift();
      if (next) {
        await prompt(next);
        return;
      }
      if (recording?.paused || session.isStreaming) return;
      const current = typeof host.context === "function" ? await host.context() : {};
      const facts = factsForContinue(current);
      const follow = nextRecordingPrompt({
        finished: recording?.finished,
        paused: recording?.paused,
        progressed,
        continues,
        verifyErrors: recording?.verify?.errors,
        pageProgressed,
        unmatchedGoalNames: facts.goal_named_unclicked,
        requiredEmptyOverlay: facts.required_empty_overlay,
      });
      const stayOnPage = recordingStayOnPage({
        verifyErrors: recording?.verify?.errors,
        pageProgressed,
        progressed,
        unmatchedGoalNames: facts.goal_named_unclicked,
        requiredEmptyOverlay: facts.required_empty_overlay,
      });
      const skillTurn = progressed && !stayOnPage;
      progressed = false;
      pageProgressed = false;
      if (!follow) {
        if (recording && !recording.finished && !recording.paused && continues >= MAX_SKILL_CONTINUES) {
          recording.status = "failed";
          recording.finished = true;
          recording.emit?.();
        }
        return;
      }
      if (skillTurn) continues += 1;
      await prompt(`${follow}\n${JSON.stringify(facts)}`);
    }
  }
  const handle = {
    prompt: async (text) => {
      const message = text || "人已继续，从当前页面接着做";
      logLine(`[pi] 人：${message.slice(0, 200)}`);
      recording?.emitThought?.({ kind: "user", text: message.slice(0, 200) });
      if (recording?.paused) recording.paused = false;
      if (recording?.status === "waiting_operator") recording.status = "recording";
      if (session.isStreaming) {
        await session.steer(message);
        return;
      }
      await prompt(message);
    },
    dispose: () => session.dispose?.(),
  };
  const goalText = `${goal.goal_text || ""}\n${goal.page_url || ""}`;
  const context = typeof host.context === "function" ? await host.context() : {};
  const early = recording?.earlySteer || [];
  if (recording) {
    recording.earlySteer = [];
    recording.pi = handle;
  }
  prompt(`${goalText}\n${JSON.stringify(factsForContinue(context))}`).catch((error) => {
    logLine(`[pi] ${error?.message || error}`);
    recording?.emitThought?.({ kind: "tool", phase: "end", ok: false, text: String(error?.message || error).slice(0, 200) });
  });
  for (const text of early) handle.prompt(text).catch((error) => logLine(`[pi] ${error?.message || error}`));
  return handle;
}
