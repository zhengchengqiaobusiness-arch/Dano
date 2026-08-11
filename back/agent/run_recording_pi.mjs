// Long-lived recording-only Pi AgentSession runtime.
// stdin/stdout are JSONL. stdout is reserved for protocol events; diagnostics use stderr.
import readline from "node:readline";
import path from "node:path";
import {
  createAgentSession,
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";
import {
  beginRecordingToolTurn,
  endRecordingToolTurn,
  recordingTools,
} from "./recording_tools.mjs";
import { installOpenAIToolCallStreamCompatibility } from "./openai_stream_compat.mjs";

const emit = (event) => process.stdout.write(`${JSON.stringify(event)}\n`);
const log = (...parts) => process.stderr.write(`[recording_pi] ${parts.join(" ")}\n`);
const CWD = process.env.DANO_RECORDING_PI_CWD || path.resolve(new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const AGENT_DIR = process.env.DANO_RECORDING_PI_AGENT_DIR || path.join(CWD, ".pi-recording-agent");

installOpenAIToolCallStreamCompatibility({
  baseUrl: process.env.DANO_PI_BASE_URL,
  onRepair: ({ toolCallCount }) => log(
    "normalized missing finish_reason from completed tool-call stream",
    `tool_calls=${toolCallCount}`,
  ),
});

const SYSTEM_PROMPT = `你是 Dano 网页录制现场的伴随分析 Agent。
你只能使用当前提供的录制工具，不具备 Shell、文件、技能、扩展、模板或上下文文件能力。
所有录制事实、FlowSpec、人工修改和验证结果都以后端工具返回的当前版本为唯一权威来源，不得凭记忆补造。
从录制开始持续完成目标解析、操作与请求的因果对齐、请求角色判定、参数来源六分类(user_input/constant/session_header/page_context/chained/computed)和依赖假设。启发式输出仅是候选，不能直接当结论。
实时任务必须调用 get_recording_delta 拉取增量；若 has_more=true，必须用 next_seq 继续分页直到 has_more=false。响应中的 __truncated_* 只表示模型投影有界，原始录制事实仍完整保存。随后通过 submit_recording_plan 的 plan.ops 提交 set_goal、set_request_role、set_param_source、set_param_required、set_param_enum、rename_field、propose_dependency、add_pitfall；字段操作在 canonical step 尚未物化时可把 request_id 填入 step_id。set_goal.goal.evidence 必须是对象数组，例如 [{"source":"goal_text","ref":"用户输入的目标"}]，不能使用字符串。参数六分类必须按证据判定：user_input 仅用于目标或 fill/select 等可编辑控件明确证明由操作人提供的业务值；录制值固定的业务常量（body/query 里的单据类型、流程 key 等）属于 constant；session_header 仅限 headers.* 路径的鉴权/会话头；未被操作人修改的 pageNo/pageSize/current/limit/offset 等分页值属于 page_context，并编译为录制默认值且允许调用方覆盖；上游响应强值被后续请求复用属于 chained，必须带 origin_request_id 和 origin_path；由其他用户参数推导的值（如天数=结束时间-开始时间）属于 computed，必须带 strategy=date_span_days_json、start_field、end_field。每个分类都会做可执行编译校验，被拒绝时按返回原因改类重提，禁止硬塞最接近的类。上游响应决定请求键结构时（如动态审批节点 ID 作为请求键），用 propose_dependency kind=structure 并把 target_path 填容器路径。必填性必须用 set_param_required、业务名称必须用 rename_field、页面字典枚举必须用 set_param_enum；三者都会与 field_evidence/字典映射逐值回检，evidence_refs 至少一条必须引用真实 request_id/event_id/step_id。禁止只在 field_semantics 里提交这些字段轴变更以绕过证据闸门。依赖只能用 propose_dependency 提出并附证据与验证计划，绝不能自行标记 verified。结论必须带 evidence_refs 或可复核 reason。提交后检查 op_results；skipped/rolled_back 都表示结论未落地，必须按 reason 修正，不能假装成功。
仅在业务事实无法自答时调用 ask_operator，一次只问一个问题；recording_id、flow_version、run_id 均由后端管理，所有工具调用必须省略这些字段，严禁向操作人询问；返回 answered=false 时按最佳假设继续并保留待验证状态，不得等待或反复追问。
规划任务必须先调用 get_recording_state，再调用 submit_recording_plan。
规划任务读取状态后禁止输出分析过程，必须立即调用提交工具。计划只提交实际变化和必要能力边界，字段优先使用紧凑 key=value;... 记录；不要复述未变化字段，不要在工具调用前写长篇说明。
修复任务必须先调用 get_validation_report；需要完整事实时再调用 get_recording_state，然后调用 submit_recording_repair。
验证任务必须逐项处理后端给出的 verification_todos：依赖链用 perturb_replay，普通读用 replay_request，写契约用 execute_write_with_verify，缺失分支或枚举用 browser_navigate/browser_snapshot/browser_click/browser_fill/browser_select 补采。dependency_candidate 是捕获事实计算出的高置信值链候选；扰动成功后必须在同一次 submit_recording_repair 中先按候选给定的 link_id 提交 propose_dependency，再用同一 link_id 和真实 verification_id 提交 confirm_dependency。只有执行器返回的 verification_id 才能用于 confirm_dependency、bind_verify_read、attach_enum_options；不得编造 ID。完成一轮后通过 submit_recording_repair 提交这些 ops；无法验证的项目留给编排器在重试耗尽后标记 unverified。

验证结束后必须基于当前 FlowSpec 事实生成自包含包文档，并调用 submit_skill_docs 提交完整 SKILL.md 和 reference.md。reference.md 的每条 API chain 必须标注真实 verification_id，无法验证的链明确写 unverified；文档不得包含凭证。
审核任务必须先调用 get_recording_state 和 get_validation_report，再调用一次 submit_recording_review；review 顶层只能包含 acceptance、security、compliance 和可选 blocking_reasons，三个角色都只能包含 passed、reasons，model_id 由服务器记录；审核不通过时使用 passed=false 和 reasons 说明，也可以增加 blocking_reasons，成功提交后立即结束本轮，禁止再次读取或重复提交。
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
const EPHEMERAL_CREDENTIALS = {
  read: async () => undefined,
  list: async () => [],
  modify: async (_provider, update) => update(undefined),
  delete: async () => undefined,
};

async function resolveModel() {
  const apiKey = process.env.DANO_PI_API_KEY;
  const baseUrl = process.env.DANO_PI_BASE_URL;
  const provider = process.env.DANO_PI_PROVIDER || "openai-compat";
  const modelId = process.env.DANO_PI_MODEL || "deepseek-ai/DeepSeek-V3.2";
  const modelRuntime = await ModelRuntime.create({
    credentials: EPHEMERAL_CREDENTIALS,
    modelsPath: null,
    allowModelNetwork: false,
  });

  if (baseUrl && apiKey) {
    modelRuntime.registerProvider(provider, {
      name: provider,
      baseUrl,
      api: "openai-completions",
      models: [{
        id: modelId,
        name: modelId,
        reasoning: false,
        input: ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: envInt("DANO_PI_CONTEXT_WINDOW", 128000, 1024),
        maxTokens: envInt("DANO_PI_MAX_TOKENS", 32768, 1),
      }],
    });
  }
  if (apiKey) await modelRuntime.setRuntimeApiKey(provider, apiKey, { allowNetwork: false });

  const model = modelRuntime.getModel(provider, modelId);
  if (!model || !apiKey) throw new Error(`no Pi model or credentials: provider=${provider} model=${modelId}`);
  return { modelRuntime, model };
}

function createSettingsManager() {
  // Retry and compaction are Pi-native. This runtime does not implement either behavior.
  return SettingsManager.inMemory({
    httpIdleTimeoutMs: 0,
    retry: {
      enabled: true,
      maxRetries: envInt("DANO_RECORDING_PI_MAX_RETRIES", 3, 0),
      baseDelayMs: envInt("DANO_RECORDING_PI_RETRY_BASE_DELAY_MS", 2000, 0),
      provider: {
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
  if (message?.errorMessage) summary.error = String(message.errorMessage).slice(0, 2000);
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

function normalizePromptImages(value) {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Error("prompt.images must be an array");
  if (value.length > 4) throw new Error("prompt.images supports at most 4 images");
  const allowed = new Set(["image/jpeg", "image/png", "image/webp"]);
  return value.map((image, index) => {
    if (!image || typeof image !== "object" || image.type !== "image") {
      throw new Error(`prompt image ${index + 1} must be an image object`);
    }
    const data = typeof image.data === "string" ? image.data : "";
    const mimeType = typeof image.mimeType === "string" ? image.mimeType.toLowerCase() : "";
    if (!data || data.length > 3_000_000 || !allowed.has(mimeType)) {
      throw new Error(`prompt image ${index + 1} is invalid or too large`);
    }
    return { type: "image", data, mimeType };
  });
}

async function startSession(command) {
  if (active) throw new Error("a recording Pi session is already active; close it before starting another");
  if (promptInFlight) throw new Error("cannot start a session while a prompt is running");

  const { modelRuntime, model } = await resolveModel();
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
    modelRuntime,
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
  const images = normalizePromptImages(command.images);
  const promptOptions = {
    expandPromptTemplates: false,
    source: "rpc",
    ...(images.length ? { images } : {}),
  };
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
    image_count: images.length,
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
