/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 这里只启动/关闭 PI 会话并投递通知。全部业务语义由 PI 通过工具自行完成。
 */

import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PiRequiredError, PI_ONLY_NOTICE, assertNeverStartLegacy, logPiOnly } from "./policy.mjs";
import { wrapPiToolsForSdk } from "./pi-tools.mjs";
import { applyPiModelConfig } from "./pi-model.mjs";
import { installOpenAIToolCallStreamCompatibility } from "./openai-stream-compat.mjs";
import { createPiTrace } from "./pi-trace.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SKILL_PATH = path.join(ROOT, "skill", "RECORDING_CAPABILITY.md");

export async function readRecordingSkill() {
  try {
    return await readFile(SKILL_PATH, "utf8");
  } catch {
    return "";
  }
}

export function buildPiInstructions(skillText = "") {
  return `${PI_ONLY_NOTICE}

你是本录制系统的绝对统治者和唯一语义权威。
最终必须产出能力。现有录制页会把你提交的 result 原样当作 draft 展示。
没有非空 capabilities，就等于没有产物。代码不会替你编造能力。

${skillText}

可用工具：
- list_recording_manifest
- list_recording_index
- read_evidence_delta
- read_evidence_item
- read_response_blob
- read_screenshot
- get_recording_freeze_state
- submit_recording_draft
- submit_recording_result

规则：
1. 用工具读取全部或增量证据，不要假设代码已经分析过。
2. 过程结论只能用 submit_recording_draft。草稿不会变成最终结果。
3. 只有证据冻结后，才能调用 submit_recording_result。
4. submit_recording_result 必须包含 recording_id、final=true、以及由你完整编写的 result。
5. result.capabilities 必须是非空数组，并且包含现有录制页能直接渲染的字段合同与请求编排。
6. 不要写 capabilities[].fields。request_refs 必须是 {step_id, usage} 对象。steps[].params 必须是含 key/path 的对象数组。调用方字段必须出现在 input_schema.properties 或这些 params 里。
7. 先调 list_recording_index 建台账，再抽读正文。每个独立业务动作都要有能力或 unresolved。capability_id 不得重复。每个能力恰好一个不共用的 execute。
8. 系统会原样保存 result，不会补齐、改写或生成替代能力。
`;
}

export const PI_INSTRUCTIONS = buildPiInstructions(await readRecordingSkill());

export function buildFinalAnalysisPrompt(latestSeq) {
  return (
    `证据已冻结，最新 seq=${Number(latestSeq) || 0}。现在必须调用 submit_recording_result。\n` +
    "先调 list_recording_index 建台账，看 interaction、xhr/fetch、network_response 和 visible_control。读关键 execute 请求正文；响应在 network_response 或读请求时附带的 response.body。\n" +
    "先读各页 visible_control，再对 execute 每个 query/body 键。树/页签/分段器/单选组/日期区间都是可改选择。可改控件一律调用方；页面自动计算但仍可手工修改的输入也属于调用方。readonly/disabled 灰框是系统，不要进 schema。每个 exposed_to_user=true 的 param 都必须出现在 schema，schema 顶层 key、param.key、param.path 的末级键必须逐字对应 execute 的真实 query/body 键，禁止相近拼写和别名。禁止编造写请求里没有的键。可增行只保留一个对象数组 key，禁止收成 string；items.properties title 用各分区表头原文，同键不同表头写 x-dano-section-titles。form textarea 不要用表格分区标题。确认弹层可填意见：有请求键就建模，没有就 unresolved，不要编新键。登录身份用 current_user，不要写死本场数字。label/title 用页面原文，去掉星号。\n" +
    "可改树/下拉/单选禁止只写 type=number。api_option 必须把 source_url 写进 param.source 和 schema 的 x-dano-option-source；page_enum 必须写当场全部 {label,value}。对象数组选择器的绑定只能写在对应 execute step.selects，禁止写到 result 顶层；必须包含 multi、label_subkey 和覆盖真实对象键的 element_template。把树/下拉藏在 description 里会被拒收。不要读 screenshot。\n" +
    "read_response_blob 只接受 body.blob_id（blob_ 开头）。不要把 request_id 当 blob_id，也不要读 screenshot 去找接口正文。\n" +
    "看完关键请求立刻把完整 result 作为 submit_recording_result 的工具参数提交。不要把 JSON 写在对话里。不要写 capabilities[].fields。request_refs 必须是 {step_id, usage}。steps[].params 必须是含 key/path 的对象数组。\n" +
    "若已有 submit_recording_draft，立刻 final=true 提交。草稿不会自动变成结果。"
  );
}

export const MAX_EMPTY_FINAL_SETTLES = 3;
const ABORT_RESIDUE_MS = 150;
const RETRY_AFTER_ABORT_MS = 80;

export function isAbortLikeError(error) {
  return /abort/i.test(String(error?.message || error || ""));
}

export function isBusyPromptError(error) {
  const message = String(error?.message || error || "");
  return message.includes("already processing") || message.includes("streamingBehavior");
}

export class LivePiSession {
  constructor({
    session,
    sessionId,
    dispose,
    notifyDebounceMs = 8000,
    instructions = PI_INSTRUCTIONS,
    onThought = null,
    trace,
  }) {
    this.session = session;
    this.sessionId = sessionId;
    this.alive = true;
    this.status = "ready";
    this.lastError = "";
    this.#instructions = instructions;
    this.#dispose = dispose;
    this.#onThought = typeof onThought === "function" ? onThought : null;
    this.#trace = trace || createPiTrace({ onThought: (payload) => this.#emitThought(payload) });
    this.#exitListeners = new Set();
    this.#notifyChain = Promise.resolve();
    this.#latestSeq = 0;
    this.#flushScheduled = false;
    this.#notifyDebounceMs = Number(notifyDebounceMs) || 8000;
    this.#unsub = typeof session?.subscribe === "function"
      ? session.subscribe((event) => this.#trace.handleEvent(event))
      : null;
  }

  #instructions;
  #dispose;
  #onThought;
  #trace;
  #exitListeners;
  #notifyChain;
  #latestSeq;
  #flushScheduled;
  #notifyDebounceMs;
  #unsub;
  #analysisSettleErr;

  #emitThought(payload) {
    if (!payload) return;
    try {
      this.#onThought?.(payload);
    } catch {
      // 助手输出失败不得影响分析
    }
  }

  onExit(listener) {
    this.#exitListeners.add(listener);
    return () => this.#exitListeners.delete(listener);
  }

  #emitExit() {
    for (const listener of this.#exitListeners) {
      try {
        listener();
      } catch {
        // 退出通知不得反向生成结果
      }
    }
  }

  async #promptNow(text) {
    if (!this.alive) {
      throw new Error("PI 会话已关闭");
    }
    try {
      await this.session.prompt(text);
      return { queued: false };
    } catch (error) {
      if (isBusyPromptError(error)) {
        await this.session.prompt(text, { streamingBehavior: "followUp" });
        return { queued: true };
      }
      throw error;
    }
  }

  notifyEvidence({ seq }) {
    this.#latestSeq = Number(seq) || this.#latestSeq;
    return this.#notifyChain;
  }

  async requestFinalAnalysis({
    timeoutMs = 600000,
    idleSubmitMs = 90000,
    hasResult,
    maxEmptySettles = MAX_EMPTY_FINAL_SETTLES,
  } = {}) {
    if (!this.alive) {
      throw new PiRequiredError("PI 会话已关闭");
    }
    this.status = "finalizing";
    const started = Date.now();
    const deadline = started + timeoutMs;
    const idleMs = Math.max(20, Number(idleSubmitMs) || 90000);
    const emptyBudget = Math.max(1, Number(maxEmptySettles) || MAX_EMPTY_FINAL_SETTLES);
    const submitNow = (
      "证据已经够了。立刻调用 submit_recording_result，把完整 result 作为工具参数提交。不要把 JSON 写在对话里，不要再读证据。"
    );
    const checkResult = typeof hasResult === "function" ? hasResult : null;
    let lastToolCount = this.#trace.toolCount;
    let lastToolAt = Date.now();
    let lastPromptAt = Date.now();
    let lastSeenTools = this.#trace.toolCount;
    let emptySettles = 0;
    let retryTimer = null;
    let steered = false;
    let interrupted = false;
    let restartAfterAbort = false;
    let settled = false;
    let resolveDone;
    let rejectDone;
    const done = new Promise((resolve, reject) => {
      resolveDone = resolve;
      rejectDone = reject;
    });
    const clearRetry = () => {
      if (retryTimer != null) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
    };
    const settleOk = () => {
      if (settled) return;
      settled = true;
      clearRetry();
      resolveDone();
    };
    const settleErr = (error) => {
      if (settled) return;
      settled = true;
      clearRetry();
      rejectDone(error);
    };
    this.#analysisSettleErr = settleErr;
    if (!this.alive) {
      settleErr(new Error("PI 会话已关闭"));
    }
    const resultReady = async () => {
      if (!checkResult) return false;
      try {
        return Boolean(await checkResult());
      } catch {
        return false;
      }
    };
    const scheduleRetry = (text) => {
      const toolsNow = this.#trace.toolCount;
      const abortResidue = interrupted && Date.now() - lastPromptAt < ABORT_RESIDUE_MS;
      if (toolsNow > lastSeenTools) {
        lastSeenTools = toolsNow;
        emptySettles = 0;
      } else if (abortResidue) {
        logPiOnly("[PI分析] 中止后的空轮不算空转，继续等待提交");
      } else {
        emptySettles += 1;
      }
      if (emptySettles >= emptyBudget) {
        logPiOnly(`[PI分析] 连续 ${emptySettles} 轮空转未提交，停止重试`);
        this.#emitThought({ kind: "text", text: "连续空转未提交，停止分析" });
        settleErr(new Error("PI 连续空转未调用工具且未提交"));
        return;
      }
      logPiOnly("[PI分析] 本轮结束但未提交，继续要求 submit_recording_result");
      this.#emitThought({ kind: "text", text: "本轮结束但未提交，继续要求提交完整能力" });
      clearRetry();
      retryTimer = setTimeout(() => {
        retryTimer = null;
        if (settled || !this.alive) return;
        startPrompt(text);
      }, abortResidue ? RETRY_AFTER_ABORT_MS : 0);
    };
    const interruptHungTurn = () => {
      if (settled || !this.alive || interrupted) return;
      interrupted = true;
      if (typeof this.session.abort !== "function") {
        logPiOnly("[PI分析] 催促后仍无新工具，当前运行时不能中止，继续等当前轮提交");
        this.#emitThought({ kind: "text", text: "当前轮仍在进行，继续等待提交" });
        lastToolAt = Date.now();
        return;
      }
      restartAfterAbort = true;
      emptySettles = 0;
      logPiOnly("[PI分析] 催促后仍无新工具，中止当前轮并要求立刻提交");
      this.#emitThought({ kind: "text", text: "分析卡住，中止当前轮并要求立刻提交" });
      Promise.resolve(this.session.abort())
        .catch(() => {})
        .finally(() => {
          if (settled || !this.alive) return;
          restartAfterAbort = false;
          startPrompt(submitNow);
        });
    };
    const onPromptSettled = async (error) => {
      if (settled) return;
      if (restartAfterAbort) return;
      if (!this.alive) {
        settleErr(new Error("PI 会话已关闭"));
        return;
      }
      if (await resultReady()) {
        settleOk();
        return;
      }
      if (!checkResult) {
        if (error && !isAbortLikeError(error)) settleErr(error);
        else if (!error) settleOk();
        return;
      }
      if (isAbortLikeError(error)) {
        return;
      }
      if (error) {
        settleErr(error);
        return;
      }
      if (Date.now() >= deadline) {
        settleErr(new Error("PI 最终分析超时"));
        return;
      }
      if (!this.alive) {
        settleErr(new Error("PI 会话已关闭"));
        return;
      }
      scheduleRetry(submitNow);
    };
    const startPrompt = (text, options) => {
      if (settled || !this.alive) return;
      lastPromptAt = Date.now();
      let task;
      try {
        task = options ? this.session.prompt(text, options) : this.#promptNow(text);
      } catch (error) {
        onPromptSettled(error);
        return;
      }
      Promise.resolve(task).then(
        (result) => {
          if (result?.queued) {
            if (!checkResult) {
              onPromptSettled();
              return;
            }
            logPiOnly("[PI分析] 当前轮仍在进行，已排队催促，不算空转");
            return;
          }
          onPromptSettled();
        },
        (error) => onPromptSettled(error),
      );
    };
    logPiOnly(`[PI分析] 开始最终分析 timeout=${timeoutMs}ms seq=${this.#latestSeq}`);
    this.#emitThought({ kind: "text", text: `开始最终分析，最新证据 seq=${this.#latestSeq}` });
    startPrompt(buildFinalAnalysisPrompt(this.#latestSeq));
    const heartbeat = setInterval(() => {
      if (!this.alive) {
        settleErr(new Error("PI 会话已关闭"));
        return;
      }
      const line = `仍在分析 ${Math.round((Date.now() - started) / 1000)}s ${this.#trace.summary()}`;
      logPiOnly(`[PI分析] ${line}`);
      this.#emitThought({ kind: "text", text: line });
    }, 15000);
    const resultWatch = checkResult
      ? setInterval(async () => {
        if (settled) return;
        if (!this.alive) {
          settleErr(new Error("PI 会话已关闭"));
          return;
        }
        if (await resultReady()) {
          logPiOnly("[PI分析] 已检测到 submit_recording_result");
          this.#emitThought({ kind: "text", text: "已检测到最终提交" });
          settleOk();
        }
      }, 250)
      : null;
    const idleWatch = setInterval(() => {
      if (settled || !this.alive) return;
      if (this.#trace.toolCount !== lastToolCount) {
        lastToolCount = this.#trace.toolCount;
        lastToolAt = Date.now();
        steered = false;
        interrupted = false;
        return;
      }
      const lastEventAt = Number(this.#trace.lastEventAt) || 0;
      if (lastEventAt > lastToolAt) {
        lastToolAt = lastEventAt;
        steered = false;
        interrupted = false;
        return;
      }
      if (this.#trace.toolCount <= 0) return;
      const quietFor = Date.now() - lastToolAt;
      if (quietFor < idleMs) return;
      if (!steered) {
        steered = true;
        lastToolAt = Date.now();
        logPiOnly(`[PI分析] ${Math.round(quietFor / 1000)}s 没有新工具，催促提交，不中止当前轮`);
        this.#emitThought({ kind: "text", text: `${Math.round(quietFor / 1000)}s 没有新工具，催促提交` });
        if (!this.alive) return;
        this.session.prompt(submitNow, { streamingBehavior: "steer" }).catch((error) => {
          logPiOnly(`[PI分析] 催促提交失败 ${error?.message || error}`);
        });
        return;
      }
      interruptHungTurn();
    }, Math.min(1000, Math.max(20, Math.floor(idleMs / 2) || 20)));
    let timeoutHandle;
    const timeoutTask = new Promise((_, reject) => {
      timeoutHandle = setTimeout(() => reject(new Error("PI 最终分析超时")), Math.max(50, timeoutMs));
    });
    try {
      await Promise.race([done, timeoutTask]);
    } catch (error) {
      this.lastError = error.message || String(error);
      const closed = !this.alive || String(this.lastError).includes("会话已关闭");
      this.status = closed ? "closed" : "failed";
      logPiOnly(`[PI分析] 失败 ${this.lastError} elapsed=${Math.round((Date.now() - started) / 1000)}s ${this.#trace.summary()}`);
      this.#emitThought({ kind: "text", text: `分析失败：${this.lastError}` });
      try {
        await this.session.abort?.();
      } catch {
        // ignore
      }
      if (closed) {
        throw new Error("PI 会话已关闭");
      }
      if (String(this.lastError).includes("超时")) {
        throw new Error(`PI 最终分析超时 ${this.#trace.summary()}`);
      }
      throw error;
    } finally {
      this.#analysisSettleErr = null;
      clearRetry();
      clearTimeout(timeoutHandle);
      clearInterval(heartbeat);
      clearInterval(idleWatch);
      if (resultWatch) clearInterval(resultWatch);
    }
    if (Date.now() > deadline) {
      throw new Error(`PI 最终分析超时 ${this.#trace.summary()}`);
    }
    logPiOnly(`[PI分析] prompt 结束 elapsed=${Math.round((Date.now() - started) / 1000)}s ${this.#trace.summary()}`);
    this.#emitThought({ kind: "text", text: `分析结束 ${this.#trace.summary()}` });
    this.status = "submitted";
  }

  async close() {
    const wasAlive = this.alive;
    this.alive = false;
    this.status = "closed";
    this.#analysisSettleErr?.(new Error("PI 会话已关闭"));
    if (!wasAlive) {
      try {
        this.#dispose?.();
      } catch {
        // ignore
      }
      return;
    }
    try {
      this.#unsub?.();
    } catch {
      // ignore
    }
    try {
      await this.session.abort?.();
    } catch {
      // ignore
    }
    try {
      this.session.dispose?.();
    } catch {
      // ignore
    }
    try {
      this.#dispose?.();
    } catch {
      // ignore
    }
  }
}

export async function createLivePiSession({ recording, tools, onThought = null }) {
  assertNeverStartLegacy();
  const agentDir = path.join(ROOT, "runtime", "pi-agent");
  const cwd = path.join(ROOT, "runtime", "pi-cwd", recording.id);
  await mkdir(agentDir, { recursive: true });
  await mkdir(cwd, { recursive: true });

  let createAgentSession;
  let SessionManager;
  let AuthStorage;
  let ModelRegistry;
  let DefaultResourceLoader;
  let defineTool;
  let Type;
  logPiOnly("正在加载 PI SDK");
  try {
    ({
      createAgentSession,
      SessionManager,
      AuthStorage,
      ModelRegistry,
      DefaultResourceLoader,
      defineTool,
    } = await import("@mariozechner/pi-coding-agent"));
    ({ Type } = await import("@sinclair/typebox"));
  } catch (error) {
    throw new PiRequiredError(`PI 无法启动：SDK 加载失败：${error.message}`, { cause: error });
  }
  logPiOnly("PI SDK 已加载");

  const authStorage = AuthStorage.create(path.join(agentDir, "auth.json"));
  const modelRegistry = ModelRegistry.create(authStorage, path.join(agentDir, "models.json"));
  const resolved = applyPiModelConfig(authStorage, modelRegistry);
  const model = resolved.model;
  installOpenAIToolCallStreamCompatibility({
    baseUrl: resolved.baseUrl,
    onRepair: ({ toolCallCount }) => logPiOnly(`已补齐 OpenAI 兼容流的 finish_reason tool_calls=${toolCallCount}`),
  });

  const instructions = buildPiInstructions(await readRecordingSkill());
  const trace = createPiTrace({ onThought });
  const customTools = wrapPiToolsForSdk(tools, defineTool, Type, trace);
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir,
    systemPromptOverride: () => instructions,
  });
  if (typeof resourceLoader.reload === "function") {
    await resourceLoader.reload();
  }

  logPiOnly("正在初始化 PI 会话");
  let created;
  try {
    created = await createAgentSession({
      cwd,
      agentDir,
      authStorage,
      modelRegistry,
      model,
      noTools: "builtin",
      tools: [
        "list_recording_manifest",
        "list_recording_index",
        "read_evidence_delta",
        "read_evidence_item",
        "read_response_blob",
        "read_screenshot",
        "get_recording_freeze_state",
        "submit_recording_draft",
        "submit_recording_result",
      ],
      customTools,
      resourceLoader,
      sessionManager: SessionManager.inMemory(),
    });
  } catch (error) {
    throw new PiRequiredError(`PI 初始化失败：${error.message}`, { cause: error });
  }

  const sessionId = String(created.session?.sessionId || `pi_${recording.id}`);
  logPiOnly(`PI 会话初始化完成 session=${sessionId}`);
  return new LivePiSession({
    session: created.session,
    sessionId,
    dispose: () => created.session?.dispose?.(),
    instructions,
    onThought,
    trace,
  });
}
