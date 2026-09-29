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
  if (Array.isArray(current?.guides)) packet.guides = current.guides;
  return packet;
}

export function nextRecordingPrompt({ finished, paused, continues, verifyErrors }) {
  if (finished || paused || continues >= MAX_SKILL_CONTINUES) return null;
  if (Array.isArray(verifyErrors) && verifyErrors.some((item) => item.code === "auth_expired")) return null;
  const errors = Array.isArray(verifyErrors) && verifyErrors.length ? `校验没过：\n${JSON.stringify(verifyErrors)}\n` : "";
  const follow = errors
    ? "目标里点到名的操作，要有这次返回的 requests 或 filled_value。还没有就继续做。有了就按这些错误改 SKILL.md、scripts/client.py、references/api.md，再跑读命令，再 verify_skill。"
    : "目标里点到名的操作，要有这次返回的 requests 或 filled_value。还没有就继续做。有了就写 SKILL.md、scripts/client.py、references/api.md，跑读命令，再 verify_skill。";
  return errors ? `${errors}${follow}` : follow;
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
      if (!follow) {
        if (recording && !recording.finished && !recording.paused) {
          recording.emitThought?.({ kind: "text", text: "处理停在当前结果，页面留在录制。" });
          recording.emit?.();
        }
        return;
      }
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
      continues = 0;
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
