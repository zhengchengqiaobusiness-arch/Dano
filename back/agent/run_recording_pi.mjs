// Long-lived recording-only Pi AgentSession runtime.
// stdin/stdout are JSONL. stdout is reserved for protocol events; diagnostics use stderr.
import readline from "node:readline";
import path from "node:path";
import {
  AuthStorage,
  createAgentSession,
  DefaultResourceLoader,
  ModelRegistry,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";
import {
  beginRecordingToolTurn,
  endRecordingToolTurn,
  recordingTools,
} from "./recording_tools.mjs";

const emit = (event) => process.stdout.write(`${JSON.stringify(event)}\n`);
const log = (...parts) => process.stderr.write(`[recording_pi] ${parts.join(" ")}\n`);
const CWD = process.env.DANO_RECORDING_PI_CWD || path.resolve(new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const AGENT_DIR = process.env.DANO_RECORDING_PI_AGENT_DIR || path.join(CWD, ".pi-recording-agent");

const SYSTEM_PROMPT = `你是 Dano 网页录制模式的专用语义编排 Agent。
你只能使用当前提供的五个录制工具，不具备 Shell、文件、技能、扩展、模板或上下文文件能力。
所有录制事实、FlowSpec、人工修改和验证结果都以后端工具返回的当前版本为唯一权威来源，不得凭记忆补造。
规划任务必须先调用 get_recording_state，再调用 submit_recording_plan。
修复任务必须先调用 get_validation_report；需要完整事实时再调用 get_recording_state，然后调用 submit_recording_repair。
审核任务必须先调用 get_recording_state 和 get_validation_report，再调用一次 submit_recording_review；review 顶层只能包含 acceptance、security、compliance，三个角色都只能包含 passed、reasons、model_id；审核不通过时使用 passed=false 和 reasons 说明，成功提交后立即结束本轮，禁止再次读取或重复提交。
不得泄漏或索取凭证，不得改写原始 URL、HTTP method、请求路径或录制事实，不得绕过版本、校验和发布闸门。
提交工具被拒绝后，必须重新读取最新状态才能纠正一次；第二次仍被拒绝必须停止本轮，不得继续反复调用。
完成对应提交工具调用后，用简短中文说明提交结果；若工具拒绝，明确说明拒绝原因，不要假装成功。`;

let active = null;
let promptInFlight = null;
let promptRequestId = null;
let promptCancelled = false;
let closing = false;

function envInt(name, fallback, minimum = 0) {
  const parsed = Number.parseInt(process.env[name] || "", 10);
  return Number.isFinite(parsed) && parsed >= minimum ? parsed : fallback;
}

const SUBMISSION_ATTEMPT_LIMIT = envInt("DANO_RECORDING_PI_MAX_SUBMISSION_ATTEMPTS", 2, 1);

function resolveModel() {
  const authStorage = AuthStorage.inMemory();
  const modelRegistry = ModelRegistry.create(authStorage);
  const apiKey = process.env.DANO_PI_API_KEY;
  const baseUrl = process.env.DANO_PI_BASE_URL;
  const provider = process.env.DANO_PI_PROVIDER || "openai-compat";
  const modelId = process.env.DANO_PI_MODEL || "deepseek-ai/DeepSeek-V3.2";

  if (baseUrl && apiKey) {
    authStorage.setRuntimeApiKey(provider, apiKey);
    modelRegistry.registerProvider(provider, {
      name: provider,
      baseUrl,
      apiKey,
      api: "openai-completions",
      models: [{
        id: modelId,
        name: modelId,
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: envInt("DANO_PI_CONTEXT_WINDOW", 128000, 1024),
        maxTokens: envInt("DANO_PI_MAX_TOKENS", 8192, 1),
      }],
    });
  } else if (apiKey) {
    authStorage.setRuntimeApiKey(provider, apiKey);
  }

  const model = modelRegistry.find(provider, modelId);
  if (!model || !apiKey) throw new Error(`no Pi model or credentials: provider=${provider} model=${modelId}`);
  return { authStorage, modelRegistry, model };
}

function createSettingsManager() {
  // Retry and compaction are Pi-native. This runtime does not implement either behavior.
  return SettingsManager.inMemory({
    retry: {
      enabled: true,
      maxRetries: envInt("DANO_RECORDING_PI_MAX_RETRIES", 3, 0),
      baseDelayMs: envInt("DANO_RECORDING_PI_RETRY_BASE_DELAY_MS", 2000, 0),
      provider: {
        timeoutMs: envInt("DANO_RECORDING_PI_PROVIDER_TIMEOUT_MS", 120000, 1),
        maxRetries: envInt("DANO_RECORDING_PI_PROVIDER_MAX_RETRIES", 2, 0),
        maxRetryDelayMs: envInt("DANO_RECORDING_PI_PROVIDER_MAX_RETRY_DELAY_MS", 30000, 0),
      },
    },
    compaction: {
      enabled: true,
      reserveTokens: envInt("DANO_RECORDING_PI_COMPACTION_RESERVE_TOKENS", 16384, 1),
      keepRecentTokens: envInt("DANO_RECORDING_PI_COMPACTION_KEEP_RECENT_TOKENS", 20000, 1),
    },
    steeringMode: "one-at-a-time",
    followUpMode: "one-at-a-time",
    enableAnalytics: false,
    enableInstallTelemetry: false,
    skills: [],
    extensions: [],
    prompts: [],
    packages: [],
  });
}

function summarizeAgentEvent(event) {
  const summary = { type: "agent_event", event: event?.type || "unknown" };
  for (const key of ["toolName", "toolCallId", "attempt", "maxAttempts", "delayMs", "reason", "willRetry", "success", "aborted"]) {
    if (event?.[key] !== undefined) summary[key] = event[key];
  }
  const message = event?.message;
  if (message?.role) summary.role = message.role;
  if (message?.stopReason) summary.stop_reason = message.stopReason;
  if (message?.usage) summary.usage = message.usage;
  if (event?.errorMessage) summary.error = String(event.errorMessage).slice(0, 2000);
  if (event?.error) summary.error = String(event.error).slice(0, 2000);
  return summary;
}

function lastAssistantText(session) {
  const messages = session.messages;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "assistant") continue;
    const content = message.content;
    if (typeof content === "string") return content;
    if (Array.isArray(content)) return content.map((item) => item?.type === "text" ? item.text || "" : "").join("");
  }
  return "";
}

function messageText(message) {
  const content = message?.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.map((item) => item?.type === "text" ? item.text || "" : "").join("");
}

function promptWasAppended(session, startIndex, text) {
  return session.messages.slice(startIndex).some((message) => (
    message?.role === "user" && messageText(message).trim() === text.trim()
  ));
}

async function startSession(command) {
  if (active) throw new Error("a recording Pi session is already active; close it before starting another");
  if (promptInFlight) throw new Error("cannot start a session while a prompt is running");

  const { authStorage, modelRegistry, model } = resolveModel();
  const settingsManager = createSettingsManager();
  const sessionDir = command.session_dir ? path.resolve(command.session_dir) : undefined;
  const sessionManager = command.session_file
    ? SessionManager.open(path.resolve(command.session_file), sessionDir, CWD)
    : SessionManager.create(CWD, sessionDir, command.session_id ? { id: command.session_id } : undefined);
  const resourceLoader = new DefaultResourceLoader({
    cwd: CWD,
    agentDir: AGENT_DIR,
    settingsManager,
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    systemPrompt: SYSTEM_PROMPT,
  });
  await resourceLoader.reload();

  const created = await createAgentSession({
    cwd: CWD,
    agentDir: AGENT_DIR,
    model,
    authStorage,
    modelRegistry,
    settingsManager,
    resourceLoader,
    sessionManager,
    customTools: recordingTools,
    noTools: "builtin",
    tools: recordingTools.map((tool) => tool.name),
  });
  const unsubscribe = created.session.subscribe((event) => emit({
    ...summarizeAgentEvent(event),
    request_id: promptRequestId,
    session_id: created.session.sessionId,
  }));
  active = { session: created.session, unsubscribe };
  emit({
    type: "session_started",
    request_id: command.request_id,
    session_id: created.session.sessionId,
    session_file: created.session.sessionFile,
    resumed: Boolean(command.session_file),
    retry: settingsManager.getRetrySettings(),
    compaction: settingsManager.getCompactionSettings(),
  });
}

async function runPrompt(command) {
  if (!active) throw new Error("no active recording Pi session");
  if (promptInFlight) throw new Error("a prompt is already running");
  if (typeof command.text !== "string" || !command.text.trim()) throw new Error("prompt.text must be a non-empty string");

  promptRequestId = command.request_id || null;
  promptCancelled = false;
  let submissionLimitError = "";
  let acceptedSubmission = "";
  const session = active.session;
  beginRecordingToolTurn({
    maxSubmissionAttempts: SUBMISSION_ATTEMPT_LIMIT,
    onLimitExceeded: (error) => {
      submissionLimitError = String(error?.message || error);
      log(submissionLimitError);
      void session.abort().catch((abortError) => log("submission limit abort failed", abortError));
    },
    onSubmissionAccepted: (toolName) => {
      acceptedSubmission = toolName;
      // The bridge call has completed and Python has persisted the authoritative
      // submission. Abort is signalled immediately: Pi still finalizes the
      // current successful tool result before observing the signal, then stops
      // the batch. There is no delayed callback that could affect a later turn.
      void session.abort().catch((abortError) => log("terminal submission abort failed", abortError));
    },
  });
  const promptOptions = { expandPromptTemplates: false, source: "rpc" };
  const startIndex = session.messages.length;
  let work = session.prompt(command.text, promptOptions);
  promptInFlight = work;
  try {
    try {
      await work;
    } catch (error) {
      const continuationBoundaryError = String(error?.message || error).includes(
        "Cannot continue from message role: assistant",
      );
      if (!continuationBoundaryError) throw error;
      if (!promptWasAppended(session, startIndex, command.text)) {
        // Pi may finish automatic compaction with an assistant message and then
        // call Agent.continue() before appending this RPC prompt. Retry exactly
        // once at the now-stable boundary; unrelated provider/runtime failures
        // are never swallowed or retried here.
        emit({
          type: "agent_event",
          event: "continuation_boundary_recovered",
          request_id: command.request_id,
          session_id: session.sessionId,
        });
        work = session.prompt(command.text, promptOptions);
        promptInFlight = work;
        await work;
      } else {
        // The prompt is already in the transcript. Retrying would execute the
        // same recording tools twice, so the completed turn is kept as-is.
        emit({
          type: "agent_event",
          event: "continuation_completion_recovered",
          request_id: command.request_id,
          session_id: session.sessionId,
        });
      }
    }
  } catch (error) {
    if (/abort/i.test(String(error?.message || error))) promptCancelled = true;
    else throw error;
  } finally {
    promptInFlight = null;
    endRecordingToolTurn();
  }
  const stats = session.getSessionStats();
  emit({
    type: "prompt_completed",
    request_id: command.request_id,
    session_id: session.sessionId,
    session_file: session.sessionFile,
    // A persisted terminal submission is authoritative even if a concurrent
    // duplicate happened to reach the limiter before cancellation completed.
    status: acceptedSubmission
      ? "submitted"
      : (submissionLimitError ? "submission_limit" : (promptCancelled ? "cancelled" : "completed")),
    ...(!acceptedSubmission && submissionLimitError ? { error: submissionLimitError } : {}),
    ...(acceptedSubmission ? { accepted_submission: acceptedSubmission } : {}),
    final_text: lastAssistantText(session).slice(0, 100000),
    usage: stats.tokens,
    session: stats,
  });
  promptRequestId = null;
  promptCancelled = false;
}

async function cancelPrompt(command) {
  if (!active) throw new Error("no active recording Pi session");
  if (promptInFlight) {
    promptCancelled = true;
    await active.session.abort();
  }
  emit({
    type: "agent_event",
    event: "cancelled",
    request_id: command.request_id,
    session_id: active.session.sessionId,
  });
}

async function closeSession(command) {
  if (!active) {
    emit({ type: "session_closed", request_id: command.request_id, session_id: null });
    return;
  }
  if (promptInFlight) await active.session.abort();
  const { session, unsubscribe } = active;
  unsubscribe?.();
  const sessionId = session.sessionId;
  const sessionFile = session.sessionFile;
  session.dispose();
  active = null;
  emit({ type: "session_closed", request_id: command.request_id, session_id: sessionId, session_file: sessionFile });
}

async function handleCommand(command) {
  if (!command || typeof command !== "object") throw new Error("command must be a JSON object");
  switch (command.type) {
    case "start_session": return startSession(command);
    case "prompt": return runPrompt(command);
    case "cancel": return cancelPrompt(command);
    case "close": return closeSession(command);
    default: throw new Error(`unsupported command type: ${String(command.type)}`);
  }
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  const trimmed = line.trim();
  if (!trimmed || closing) return;
  let command;
  try {
    command = JSON.parse(trimmed);
  } catch (error) {
    emit({ type: "runtime_error", error: `invalid JSON: ${error.message}` });
    return;
  }
  void handleCommand(command).catch((error) => {
    emit({
      type: "runtime_error",
      request_id: command.request_id,
      command: command.type,
      session_id: active?.session.sessionId || null,
      error: String(error?.message || error).slice(0, 4000),
    });
    log(error?.stack || error);
    if (command.type === "prompt") {
      promptInFlight = null;
      promptRequestId = null;
      promptCancelled = false;
    }
  });
});

rl.on("close", () => {
  closing = true;
  void closeSession({}).finally(() => process.exit(0));
});

process.on("SIGTERM", () => {
  closing = true;
  void closeSession({}).finally(() => process.exit(0));
});

process.on("SIGINT", () => {
  closing = true;
  void closeSession({}).finally(() => process.exit(0));
});
