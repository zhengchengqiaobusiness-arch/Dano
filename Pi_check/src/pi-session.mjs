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
7. 系统会原样保存 result，不会补齐、改写或生成替代能力。
`;
}

export const PI_INSTRUCTIONS = buildPiInstructions(await readRecordingSkill());

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class LivePiSession {
  constructor({ session, sessionId, dispose, notifyDebounceMs = 8000, instructions = PI_INSTRUCTIONS }) {
    this.session = session;
    this.sessionId = sessionId;
    this.alive = true;
    this.status = "ready";
    this.lastError = "";
    this.#instructions = instructions;
    this.#dispose = dispose;
    this.#exitListeners = new Set();
    this.#notifyChain = Promise.resolve();
    this.#latestSeq = 0;
    this.#flushScheduled = false;
    this.#notifyDebounceMs = Number(notifyDebounceMs) || 8000;
  }

  #instructions;
  #dispose;
  #exitListeners;
  #notifyChain;
  #latestSeq;
  #flushScheduled;
  #notifyDebounceMs;

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
    try {
      await this.session.prompt(text);
    } catch (error) {
      const message = String(error?.message || error);
      if (message.includes("already processing") || message.includes("streamingBehavior")) {
        await this.session.prompt(text, { streamingBehavior: "followUp" });
        return;
      }
      throw error;
    }
  }

  notifyEvidence({ seq }) {
    this.#latestSeq = Number(seq) || this.#latestSeq;
    return this.#notifyChain;
  }

  async requestFinalAnalysis({ timeoutMs = 240000 } = {}) {
    if (!this.alive) {
      throw new PiRequiredError("PI 在录制期间退出");
    }
    this.status = "finalizing";
    const deadline = Date.now() + timeoutMs;
    const finalPrompt = this.#promptNow(
      `${this.#instructions}\n证据已冻结，最新 seq=${this.#latestSeq}。现在必须调用 submit_recording_result。\n` +
        "优先读 list_recording_manifest、interaction 和 /admin-api/ 的 xhr/fetch 证据。不要逐条读 console。\n" +
        "提交前自检：不要写 capabilities[].fields；request_refs 必须是 {step_id, usage}；steps[].params 必须是含 key/path 的对象数组；人填过的筛选/表单必须出现在 input_schema.properties 或这些 params。\n" +
        "若你已经写过 submit_recording_draft，把完整 result 立刻提交为 final=true。草稿不会自动变成结果。这是唯一结果来源。",
    );
    const timeoutTask = sleep(Math.max(1000, timeoutMs)).then(() => {
      throw new Error("PI 最终分析超时");
    });
    try {
      await Promise.race([finalPrompt, timeoutTask]);
    } catch (error) {
      this.lastError = error.message || String(error);
      this.status = "failed";
      if (String(this.lastError).includes("超时")) {
        throw new Error("PI 最终分析超时");
      }
      throw error;
    }
    if (Date.now() > deadline) {
      throw new Error("PI 最终分析超时");
    }
    this.status = "submitted";
  }

  async close() {
    if (!this.alive) {
      try {
        this.#dispose?.();
      } catch {
        // ignore
      }
      return;
    }
    this.alive = false;
    this.status = "closed";
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

export async function createLivePiSession({ recording, tools }) {
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
  const customTools = wrapPiToolsForSdk(tools, defineTool, Type);
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
  });
}
