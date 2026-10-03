import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { dataRoot, packageRoot, recordingDir } from "../paths.mjs";
import { readGoal } from "../evidence/store.mjs";
import { applyPiModelConfig } from "./pi-model.mjs";
import { installOpenAIToolCallStreamCompatibility } from "./openai-stream-compat.mjs";
import { hostTools, toolNames, wrapHostTools } from "./tools.mjs";
import { browserSession } from "../browser/session.mjs";
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

export function continueIncludesTree(lastTool) {
  const lastName = String(lastTool || "").split(" ")[0];
  return lastName.startsWith("browser_") || lastName.startsWith("network_") || lastName === "evidence_get" || lastName === "assist" || lastName === "read_page_asset";
}

export function factsForContinue(facts) {
  if (!facts) return facts;
  return {
    goal: facts.goal,
    requests: facts.requests,
    filled: facts.filled,
    clicked: facts.clicked,
    commands: facts.commands || [],
    ran: facts.ran || [],
    snapshot: facts.snapshot
      ? {
          ...(facts.snapshot.evidence_id ? { evidence_id: facts.snapshot.evidence_id } : {}),
          epoch: facts.snapshot.epoch ?? 0,
        }
      : facts.snapshot,
  };
}

export function continueCountAfterSkillTool(continues, { skillTool, enteredSkill }) {
  if (skillTool && !enteredSkill) return 0;
  return continues;
}

export function nextRecordingPrompt({ finished, paused, progressed, continues, verifyErrors }) {
  if (finished || paused || continues >= MAX_SKILL_CONTINUES) return null;
  const errors = Array.isArray(verifyErrors) && verifyErrors.length ? `\n${JSON.stringify(verifyErrors)}` : "";
  const lead = progressed ? "页面操作的总结不是结束。" : "Skill 还没产出。";
  return `${lead}程序不替你判断来源。read_guide 读完名单。对照目标原文、requests（方法、路径、证据 id、keys、empty、issues_credential、issued）、filled 和 clicked 里的控件名，写三个文件。SKILL.md 从 --- 起行，写 name 和 description，再写一行 ---。要采用的请求用 network_get 打开全文。调用方会执行的命令要带上 keys：来自参数，或命令里先按证据再读。empty 里的键传空。录到的字面值不写进默认参数。没出现在采用请求里的填写不要写进可执行命令。同一子命令写一行，会变的参数写成 <参数名>，跑一次即可。issues_credential 的请求按 auth.local.json 的 credential 在业务请求前重放；issued 里的访问凭证字段写入 headers，刷新凭证按字段名更新 url 查询串。失败或 401 再停止。DANO_AUTH_HEADERS 只覆盖同名头。scripts/client.py 只用 Python 标准库。有读命令就跑读命令再 verify_skill。本场只有写入请求时，写带 --confirm 的命令并直接 verify_skill，不要发明没录到的读接口。${errors}`;
}

let startOverride = null;

export async function piFacts(host, { takeSnapshot = false } = {}) {
  if (takeSnapshot && typeof host.browser_snapshot === "function") {
    await host.browser_snapshot();
  }
  const current = typeof host.context === "function" ? await host.context() : {};
  return {
    goal: current.goal || {},
    requests: current.requests || [],
    filled: current.filled || [],
    clicked: current.clicked || [],
    commands: current.commands || [],
    ran: current.ran || [],
    snapshot: current.snapshot || null,
    index: current.index || [],
  };
}

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
  let enteredSkill = false;
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
      if (SKILL_TOOLS.has(event.toolName)) {
        continues = continueCountAfterSkillTool(continues, { skillTool: true, enteredSkill });
        enteredSkill = true;
        progressed = true;
      }
      lastTool = toolLine(event.toolName, event.args);
      logLine(`[pi] 调用 ${lastTool}`);
      recording?.emitThought?.({ kind: "tool", phase: "start", text: lastTool });
    }
    if (type === "tool_execution_end") {
      const name = event.toolName || lastTool || "tool";
      const line = `${toolLine(name, event.args) || name} ${resultLine(event.result)}`;
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
  let closed = false;
  async function prompt(text) {
    if (closed || recording?.finished) return;
    if (recording?.paused || prompting) {
      queued.push(text);
      return prompting;
    }
    try {
      prompting = session.prompt(text);
      await prompting;
    } catch (error) {
      if (closed || recording?.finished) return;
      if (!session.isStreaming) throw error;
      await session.steer(text);
    } finally {
      prompting = null;
      if (closed || recording?.finished) return;
      const next = queued.shift();
      if (next) {
        await prompt(next);
        return;
      }
      if (recording?.paused) return;
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
      logLine(`[pi] 继续 ${continues}`);
      const current = factsForContinue(await piFacts(host));
      await prompt(`${follow}\n${JSON.stringify(current)}`);
    }
  }
  const handle = {
    prompt: async (text) => {
      if (closed || recording?.finished) return;
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
    dispose: () => {
      closed = true;
      if (recording) recording.finished = true;
      session.dispose?.();
    },
  };
  const goalText = `${goal.goal_text || ""}\n${goal.page_url || ""}`;
  const facts = await piFacts(host, { takeSnapshot: Boolean(browserSession(recordingId)) });
  const early = recording?.earlySteer || [];
  if (recording) {
    recording.earlySteer = [];
    recording.pi = handle;
  }
  prompt(`${goalText}\n${JSON.stringify(facts)}`).catch((error) => {
    logLine(`[pi] ${error?.message || error}`);
    recording?.emitThought?.({ kind: "text", text: String(error?.message || error) });
  });
  for (const text of early) handle.prompt(text).catch((error) => logLine(`[pi] ${error?.message || error}`));
  return handle;
}
