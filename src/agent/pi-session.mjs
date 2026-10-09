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
    if (errors.length) {
      const codes = errors.map((item) => [item.code, item.name, item.field, item.path, item.file, item.key, item.command].filter(Boolean).join(":")).slice(0, 8).join(" ");
      return `失败 ${codes}`.slice(0, 500);
    }
    if (body?.ok === false) {
      const extra = [body.error, body.stderr, body.stdout, ...(body.names || []), ...(body.writable || [])].filter(Boolean).join(" ");
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

const SKILL_TOOLS = new Set(["write_skill_file", "run_skill_command", "verify_skill"]);
const MAX_SKILL_CONTINUES = 8;

export function factsForContinue(context) {
  const snapshot = context?.snapshot;
  const text = snapshot?.body?.text || snapshot?.text || "";
  return {
    goal: context?.goal || {},
    listed: context?.listed || [],
    clicked: context?.clicked || [],
    filled: context?.filled || [],
    overlay_fields: Array.isArray(context?.overlay_fields) ? context.overlay_fields.slice(0, 40) : [],
    empty_overlay: Array.isArray(context?.empty_overlay) ? context.empty_overlay.slice(0, 20) : [],
    required_empty_overlay: Array.isArray(context?.required_empty_overlay) ? context.required_empty_overlay.slice(0, 20) : [],
    snapshot: String(text).slice(0, 12000),
  };
}

let methodPack = "";

async function loadMethodPack() {
  if (methodPack) return methodPack;
  const [promptText, playwright, derive] = await Promise.all([
    readFile(path.join(packageRoot(), "src", "agent", "prompt.md"), "utf8"),
    readFile(path.join(packageRoot(), "skill", "playwright-cli", "SKILL.md"), "utf8"),
    readFile(path.join(packageRoot(), "skill", "derive-client", "SKILL.md"), "utf8"),
  ]);
  methodPack = [promptText, playwright, derive].join("\n\n");
  return methodPack;
}

export async function recordingSystemPrompt(goal) {
  return `${await loadMethodPack()}\n\n${JSON.stringify(goal)}`;
}

export function nextRecordingPrompt({ finished, paused, progressed, continues, verifyErrors }) {
  if (finished || paused || continues >= MAX_SKILL_CONTINUES) return null;
  const errors = Array.isArray(verifyErrors) && verifyErrors.length ? `\n${JSON.stringify(verifyErrors)}` : "";
  if (Array.isArray(verifyErrors) && verifyErrors.length) {
    if (verifyErrors.every((item) => item.code === "auth_expired")) {
      return `三文件已写出。打包凭证过期，不要 assist，不要改脚本猜 token，不要把凭证写进对话。结构校验已通过。${errors}`;
    }
    return `校验失败。只改 SKILL.md、scripts/client.py、references/api.md，再 run_skill_command。不要 browser_open 本地路径。SKILL.md 必须以 --- 包住 name 和 description。引用 path 的那个函数里要写出请求键名。${errors}`;
  }
  const lead = progressed
    ? "页面操作的总结不是结束。对照 listed（本场全部 xhr/fetch 索引）、empty_overlay、required_empty_overlay、overlay_fields 和目标原文写三文件。问句覆盖 overlay 里调用方填的控件和 adopted 请求的每一个键；嵌套数组保持为行，不要压成一个字符串。listed 某键这次 empty/[] 只说明这次传了空，同场另有选项请求则再读，不要冻成空数组。手册必填必须和 argparse 一致。SKILL.md 里出现的每一条读命令（含表格）都要跑。正文过大时 network_get 会省略，用 evidence_get 同 id。成品必须可调用。目标只要求写入时不要发明 GET。filled 里的控件名不是调用方必填清单。"
    : "Skill 还没产出。";
  return `${lead}read_guide 读完名单。要采用的请求打开全文。来源标签和命令行谁填必须一致。当前账号、部门、权限从 listed 已录请求取出再填，不要冻成字面量。empty 里的键传空。issues_credential 的请求按 auth.local.json 的 credential 在业务请求前重放，带上 credential.headers，失败或 401 再停止。DANO_AUTH_HEADERS 只覆盖同名头。scripts/client.py 只用 Python 标准库。跑读命令（若目标含读），再 verify_skill。${errors}`;
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
  let continues = 0;
  session.subscribe?.((event) => {
    const type = String(event?.type || "");
    const assistant = event?.assistantMessageEvent;
    if (type === "message_update" && assistant?.type === "thinking_delta" && assistant.delta) {
      recording?.emitThought?.({ kind: "thinking", text: redactSecrets(assistant.delta), stream: true });
    }
    if (type === "message_update" && assistant?.type === "text_delta" && assistant.delta) {
      recording?.emitThought?.({ kind: "text", text: redactSecrets(assistant.delta), stream: true });
    }
    if (type === "tool_execution_start") {
      if (SKILL_TOOLS.has(event.toolName)) progressed = true;
      lastTool = toolLine(event.toolName, event.args);
      logLine(`[pi] 调用 ${lastTool}`);
      recording?.emitThought?.({ kind: "tool", phase: "start", text: lastTool });
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
      if (!session.isStreaming) throw error;
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
      const follow = nextRecordingPrompt({
        finished: recording?.finished,
        paused: recording?.paused,
        progressed,
        continues,
        verifyErrors: recording?.verify?.errors,
      });
      progressed = false;
      if (!follow) {
        if (recording && !recording.finished && !recording.paused && continues >= MAX_SKILL_CONTINUES) {
          recording.status = "failed";
          recording.finished = true;
          recording.emit?.();
        }
        return;
      }
      continues += 1;
      const current = typeof host.context === "function" ? await host.context() : {};
      await prompt(`${follow}\n${JSON.stringify(factsForContinue(current))}`);
    }
  }
  const handle = {
    prompt: async (text) => {
      const message = text || "人已继续，从当前页面接着做";
      logLine(`[pi] 人：${message.slice(0, 200)}`);
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
    recording?.emitThought?.({ kind: "text", text: String(error?.message || error) });
  });
  for (const text of early) handle.prompt(text).catch((error) => logLine(`[pi] ${error?.message || error}`));
  return handle;
}
