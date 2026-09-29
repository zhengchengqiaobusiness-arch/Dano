import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { dataRoot, packageRoot, recordingDir } from "../paths.mjs";
import { readGoal } from "../evidence/store.mjs";
import { applyPiModelConfig } from "./pi-model.mjs";
import { installOpenAIToolCallStreamCompatibility } from "./openai-stream-compat.mjs";
import { hostTools, toolNames, wrapHostTools } from "./tools.mjs";
import { logLine } from "../log.mjs";

function toolLine(toolName, args) {
  const action = args?.action ? ` ${args.action}` : "";
  const ref = args?.ref ? ` ${args.ref}` : "";
  return `${toolName}${action}${ref}`;
}

function resultLine(result) {
  const text = result?.content?.find?.((item) => item?.type === "text")?.text || "";
  if (!text) return result?.isError ? "失败" : "完成";
  try {
    const body = JSON.parse(text);
    if (Array.isArray(body?.errors) && body.errors.length) {
      const codes = body.errors.map((item) => [item.code, item.name, item.field, item.path, item.file, item.key, item.command].filter(Boolean).join(":")).slice(0, 8).join(" ");
      return `失败 ${codes}`.slice(0, 500);
    }
    if (body?.ok === false) {
      const extra = [body.error, ...(body.names || []), ...(body.writable || [])].filter(Boolean).join(" ");
      return `失败 ${extra}`.slice(0, 500);
    }
    if (body?.ok === true && body.clicked) {
      const quiet = Array.isArray(body.requests) && body.requests.length === 0 ? " 无请求" : "";
      return `完成 ${body.clicked}${quiet}`;
    }
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

export function modelContextPacket(current, { index = false } = {}) {
  const packet = {
    goal: current?.goal || {},
    requests: current?.requests || [],
    filled: current?.filled || [],
  };
  if (index && Array.isArray(current?.index)) packet.index = current.index;
  if (current?.snapshot?.text) packet.snapshot = { evidence_id: current.snapshot.evidence_id || "", text: current.snapshot.text };
  if (Array.isArray(current?.filled_value) && current.filled_value.length) packet.filled_value = current.filled_value;
  if (Array.isArray(current?.also_changed) && current.also_changed.length) packet.also_changed = current.also_changed;
  if (current?.auth_file) packet.auth_file = current.auth_file;
  return packet;
}

export function nextRecordingPrompt({ finished, paused, progressed, continues, verifyErrors }) {
  if (finished || paused || continues >= MAX_SKILL_CONTINUES) return null;
  if (Array.isArray(verifyErrors) && verifyErrors.some((item) => item.code === "auth_expired")) return null;
  const errors = Array.isArray(verifyErrors) && verifyErrors.length ? `校验没过：\n${JSON.stringify(verifyErrors)}\n` : "";
  const lead = progressed ? "页面操作的总结不是结束。" : "Skill 还没产出。";
  return `${errors}${lead}read_guide 读完名单。对照目标原文、索引里的 requests（方法、路径、证据 id、keys、changed_keys、added_keys、changed_by、empty、issues_credential、actions）、filled 里的控件名和 filled_value，写三个文件。filled_value 是控件收下的值，和填进去的字不同时以它为准。also_changed 是同一次填写或点击里另外自己变了的输入控件，不是这次点中或填进去的。fields 上的 [required]、min、max、step 是控件自己标的。actions 是当时那一下点击或填写的名字，带 popup 的是弹层里的那一下，不是页面上的同名按钮。索引里也会留下没有挂在某一下动作上、但只出现过一两次的请求。changed_keys 是同一条 path、同一组键和上一次相比变了的键。added_keys 是这组键比上一条同 path 多出来的键。changed_by 记下这个键变化时正在进行的点击或填写。同一条 path 若 keys 或 empty 不同，索引里是两行，写成两个命令。没有出现在 changed_keys 里的键，不要写成固定值，也不要用空字符串或 0 顶上。SKILL.md 从 --- 起行，写 name 和 description，再写一行 ---。要采用的请求用 network_get 打开全文。references/api.md 里每条 path 旁边写证据 id，并写清是什么、调用方提供什么、请求值从哪来、依据是哪次动作。来源没分清就放未解决。调用方会执行的命令要带上 keys：来自参数，或命令里先按证据再读。引用这条 path 的函数里要逐个写出这些键名，只写 data=data 不算写过。empty 里的键传空。录到的字面值不写进默认参数。filled 里每个引号中的控件名在 SKILL.md 各占一行必填，开头的 * 是必填标记，写名字本身。popup 和 row 标明所在层和行，不是字段名。同一子命令写一行，会变的参数写成 <参数名>，跑一次即可。issues_credential 时，校验错误上的 auth_file 只有 method、path，以及 query 是否存在。可执行代码里写 credential = auth["credential"]，再 Request(credential["url"], method=credential["method"])，注释不算。query 为真时用这整段 url，不要用 base_url 另拼 path。响应带回新的刷新凭证时，只替换 credential["url"] 里原来的查询值再写回。当前脚本改过之后，先用现在的脚本再跑一次非写入命令；这次仍失败或 401 再停止。查询串里的凭证不在这里。DANO_AUTH_HEADERS 只覆盖同名头。scripts/client.py 只用 Python 标准库。跑读命令，再 verify_skill。`;
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
  const promptText = await readFile(path.join(packageRoot(), "src", "agent", "prompt.md"), "utf8");
  const goal = await readGoal(recordingId);
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir,
    systemPromptOverride: () => [promptText, JSON.stringify(goal)].join("\n\n"),
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
      recording?.emitThought?.({ kind: "thinking", text: assistant.delta, stream: true });
    }
    if (type === "message_update" && assistant?.type === "text_delta" && assistant.delta) {
      recording?.emitThought?.({ kind: "text", text: assistant.delta, stream: true });
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
      if (!follow) return;
      continues += 1;
      const current = typeof host.context === "function" ? await host.context() : {};
      await prompt(`${follow}\n${JSON.stringify(modelContextPacket(current, { index: true }))}`);
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
  prompt(`${goalText}\n${JSON.stringify(modelContextPacket(context, { index: true }))}`).catch((error) => {
    logLine(`[pi] ${error?.message || error}`);
    recording?.emitThought?.({ kind: "text", text: String(error?.message || error) });
  });
  for (const text of early) handle.prompt(text).catch((error) => logLine(`[pi] ${error?.message || error}`));
  return handle;
}
