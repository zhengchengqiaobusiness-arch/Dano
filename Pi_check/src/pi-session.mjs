/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 这里只启动/关闭 PI 会话并投递通知。全部业务语义由 PI 通过工具自行完成。
 */

import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PiRequiredError, PI_ONLY_NOTICE, assertNeverStartLegacy, logPiOnly } from "./policy.mjs";
import { describeExportPiTools, wrapPiToolsForSdk } from "./pi-tools.mjs";
import { applyPiModelConfig } from "./pi-model.mjs";
import { installOpenAIToolCallStreamCompatibility } from "./openai-stream-compat.mjs";
import { createPiTrace } from "./pi-trace.mjs";
import { HUMAN_STEER_MS, isUsefulAssistantThought } from "./browser-actions.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export function recordingPiAgentDir() {
  return path.join(ROOT, "runtime", "pi-agent");
}

export function exportPiAgentDir() {
  return path.join(ROOT, "runtime", process.env.PI_EXPORT_AGENT_DIR || "pi-agent-export");
}

export const RECORDING_SKILL_FILES = [
  "BUSINESS_SKILL_INVESTIGATOR.md",
  "CONTROL_IN_APP_BROWSER.md",
  "INFER_BUSINESS_CONTRACT.md",
];
export const EXPORT_SKILL_FILE = "BUILD_AND_VALIDATE_DEDICATED_SKILL.md";
export const REQUIRED_SKILL_FILES = [
  ...RECORDING_SKILL_FILES,
  EXPORT_SKILL_FILE,
];

export async function readRequiredSkills(skillDir = path.join(ROOT, "skill")) {
  const loaded = [];
  for (const name of REQUIRED_SKILL_FILES) {
    const file = path.join(skillDir, name);
    let text;
    try {
      text = await readFile(file, "utf8");
    } catch {
      throw new Error(`缺少 Skill 文件: ${name}`);
    }
    if (!String(text || "").trim()) {
      throw new Error(`Skill 文件为空: ${name}`);
    }
    loaded.push({ name, text: text.trim() });
  }
  return loaded;
}

export function buildPiInstructions(skills = []) {
  const bodies = (Array.isArray(skills) ? skills : []).map((item) => {
    if (typeof item === "string") return item.trim();
    const name = String(item?.name || "").replace(/\.md$/i, "");
    const text = String(item?.text || "").trim();
    return name ? `## ${name}\n\n${text}` : text;
  }).filter(Boolean);
  return `${PI_ONLY_NOTICE}

你是 Business Skill Investigator。动手按 Control In App Browser，认产物按 Infer Business Contract。
人同时也可以点预览。不要锁死预览。
没有非空 capabilities，就等于没有产物。代码不会替你编造能力。
台账齐了就 submit_recording_result 交出能力并停止。禁止写消费者 Skill 包。出包由用户在页面点击「产出 Skill」后另开 Skill 4 会话完成。

${bodies.join("\n\n")}

可用工具：
- control_in_app_browser
- read_page_asset
- list_recording_manifest
- list_recording_index
- list_action_timeline
- read_request_shape
- read_visible_controls
- read_evidence_delta
- read_evidence_item
- read_response_blob
- read_screenshot
- get_recording_freeze_state
- submit_recording_capability
- submit_recording_result
`;
}

export function buildExportPiInstructions(skillText = "") {
  return `${PI_ONLY_NOTICE}

你是 Build and Validate Dedicated Skill。不要点页面，不要交能力，不要 submit_recording_result。
唯一输入是 read_export_contract 返回的合同五块。
运输层已按合同物化整包。禁止重写 client/runtime/flow/CONTRACT/INPUT_FORMS。
写包前必须 read_generator_guides，读完返回的全部文件。
通过验证后 submit_skill_export。

${String(skillText || "").trim()}

可用工具：
- read_generator_guides
- read_export_contract
- read_skill_artifact
- write_skill_artifact
- validate_skill_package
- project_contract_to_request
- run_isolated_script
- submit_skill_export
`;
}

const ALL_REQUIRED_SKILLS = await readRequiredSkills();
export const PI_INSTRUCTIONS = buildPiInstructions(
  ALL_REQUIRED_SKILLS.filter((item) => RECORDING_SKILL_FILES.includes(item.name)),
);

export function buildUserSteerPrompt(text, { finalizing = false } = {}) {
  const body = String(text || "").trim();
  if (finalizing) {
    return (
      `用户说：${body}\n` +
      `先用一两句话回答用户。按 Skill 1 继续。台账齐了就 submit_recording_result({final:true, use_draft:true}) 交出能力。不要写消费者包。`
    );
  }
  return (
    `用户说：${body}\n` +
    `这是对话。先用一两句话回答这句话，然后按 Skill 1 继续。人也可以点预览。未接到用户结束，禁止 submit_recording_result。`
  );
}

export function buildLiveDrivePrompt({ targetUrl = "", goal = "" } = {}) {
  return (
    `你是 Business Skill Investigator。按 Skill 1–3 交能力。\n` +
    `目标：${String(goal || "").trim() || "把该页独立业务动作做成可调用能力"}\n` +
    `入口：${String(targetUrl || "").trim()}\n` +
    `人也可以点预览。未接到用户结束，禁止 submit_recording_result。\n` +
    `不要写消费者包，不要调 Skill 4。不要把完整 JSON 写在对话里。`
  );
}

export function buildFinalAnalysisPrompt(latestSeq) {
  return (
    `用户已结束。证据已冻结，最新 seq=${Number(latestSeq) || 0}。\n` +
    `按 Skill 1 对台账，Skill 3 认产物。台账齐了就提交能力。不要写消费者包，不要调 Skill 4。\n` +
    `未交出完整能力不要 submit_recording_result。不要把 JSON 写在对话里。`
  );
}

export const MAX_EMPTY_FINAL_SETTLES = 3;
export const INSTANT_EMPTY_MS = 300;
export const INSTANT_EMPTY_BUDGET = 2;
const RETRY_AFTER_EMPTY_MS = 1200;
const RETRY_AFTER_INSTANT_MS = 80;
const RETRY_AFTER_ABORT_MS = 80;

export function isAbortLikeError(error) {
  return /abort/i.test(String(error?.message || error || ""));
}

export function isEmptySpinError(error) {
  return /transport_idle|连续空转未调用工具且未提交/.test(String(error?.message || error || ""));
}

export function isInstantEmptyTurn({ elapsedMs = 0, hadNewTools = false, abortResidue = false } = {}) {
  return !hadNewTools && !abortResidue && Number(elapsedMs) < INSTANT_EMPTY_MS;
}

export function isAbortResidueTurn({
  interrupted = false,
  hadNewTools = false,
  hadModelText = false,
} = {}) {
  return Boolean(interrupted) && !hadNewTools && !hadModelText;
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
    this.lastStopReason = "";
    this.#instructions = instructions;
    this.#dispose = dispose;
    this.#onThought = typeof onThought === "function" ? onThought : null;
    this.#trace = trace || createPiTrace({ onThought: (payload) => this.#emitThought(payload) });
    this.#exitListeners = new Set();
    this.#notifyChain = Promise.resolve();
    this.#latestSeq = 0;
    this.#flushScheduled = false;
    this.#notifyDebounceMs = Number(notifyDebounceMs) || 8000;
    this.#driveStopped = false;
    this.#driveSettleOk = null;
    this.#driveEmptySettles = 0;
    this.#instantEmptySettles = 0;
    this.#lastHumanSteerAt = 0;
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
  #driveStopped;
  #driveSettleOk;
  #driveEmptySettles;
  #instantEmptySettles;
  #lastHumanSteerAt;

  get driveStopped() {
    return this.#driveStopped;
  }

  get isDriving() {
    return this.status === "driving" && !this.#driveStopped;
  }

  get needsFreshSession() {
    return this.lastStopReason === "instant_empty";
  }

  #emitThought(payload) {
    if (!payload) return;
    if (!isUsefulAssistantThought(payload)) return;
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

  async #promptFresh(text) {
    if (!this.alive) {
      throw new Error("PI 会话已关闭");
    }
    try {
      await this.session.prompt(text);
      return { queued: false };
    } catch (error) {
      if (!isBusyPromptError(error)) throw error;
      try {
        await this.session.abort?.();
      } catch {
        // 中止残留轮失败仍要再开一轮对话
      }
      await this.session.prompt(text);
      return { queued: false };
    }
  }

  async #abortLeftoverTurn() {
    try {
      await this.session.abort?.();
    } catch {
      // 没有残留轮也不挡用户发话
    }
  }

  notifyEvidence({ seq }) {
    this.#latestSeq = Number(seq) || this.#latestSeq;
    return this.#notifyChain;
  }

  notifyHumanAct({ seq } = {}) {
    this.#latestSeq = Number(seq) || this.#latestSeq;
    if (!this.alive || this.status !== "driving" || this.#driveStopped) return this.#notifyChain;
    const now = Date.now();
    if (now - this.#lastHumanSteerAt < HUMAN_STEER_MS) return this.#notifyChain;
    this.#lastHumanSteerAt = now;
    this.session.prompt(
      "用户刚在预览里操作了页面。看 snapshot.recentUserActions，用 choose/click 继续。不要每点一次就截图，也不要锁预览。",
      { streamingBehavior: "steer" },
    ).catch(() => {});
    return this.#notifyChain;
  }

  async notifyUserMessage(text = "") {
    const message = String(text || "").trim().slice(0, 2000);
    if (!message) return { ok: false, error: "请输入要发给 PI 的话" };
    if (!this.alive) return { ok: false, error: "PI 会话已关闭" };
    this.#driveEmptySettles = 0;
    this.#instantEmptySettles = 0;
    this.#emitThought({ kind: "user", text: message });
    const finalizing = this.status === "finalizing";
    const prompt = buildUserSteerPrompt(message, { finalizing });
    if (finalizing) {
      try {
        await this.#promptNow(prompt);
        return { ok: true, resumeDrive: false, text: message };
      } catch (error) {
        return { ok: false, error: error.message || String(error) };
      }
    }
    if (this.status === "submitted") {
      return { ok: false, error: "录制已结束，不能再发给 PI" };
    }
    if (this.status === "driving" && !this.#driveStopped) {
      try {
        this.session.prompt(prompt, { streamingBehavior: "steer" }).catch(() => {});
        this.#emitThought({ kind: "text", text: "已收到，正在按你的话继续" });
        return { ok: true, resumeDrive: false, text: message };
      } catch (error) {
        return { ok: false, error: error.message || String(error) };
      }
    }
    await this.#abortLeftoverTurn();
    this.#emitThought({ kind: "text", text: "已收到，正在按你的话继续" });
    return { ok: true, resumeDrive: true, text: message };
  }

  async abortLiveWork() {
    if (!this.alive) return { ok: false, error: "PI 会话已关闭" };
    this.#emitThought({
      kind: "text",
      text: "用户终止了当前自动操作。预览你继续点，或再发一句话让我继续。",
    });
    this.lastStopReason = "user";
    this.#driveStopped = true;
    try {
      await this.session.abort?.();
    } catch {
      // 中止失败仍要停自动点
    }
    this.#driveSettleOk?.();
    if (this.status === "driving") this.status = "ready";
    return { ok: true };
  }

  async pauseForAssist(reason = "") {
    if (!this.alive) return { ok: false, error: "PI 会话已关闭" };
    const message = String(reason || "请在预览协助").trim();
    this.#emitThought({
      kind: "text",
      text: `已暂停自动操作。${message}。预览你继续点，做完后说继续。`,
    });
    this.lastStopReason = "assist";
    this.#driveStopped = true;
    try {
      await this.session.abort?.();
    } catch {
      // 中止失败仍要停自动点
    }
    this.#driveSettleOk?.();
    if (this.status === "driving") this.status = "ready";
    return { ok: true };
  }

  async beginLiveDrive({
    targetUrl = "",
    goal = "",
    resumeHint = "",
    timeoutMs = 600000,
    idleSubmitMs = 90000,
    hasResult,
    maxEmptySettles = MAX_EMPTY_FINAL_SETTLES,
  } = {}) {
    if (!this.alive) {
      throw new PiRequiredError("PI 会话已关闭");
    }
    if (this.isDriving) {
      if (resumeHint) {
        this.#driveEmptySettles = 0;
        this.session.prompt(buildUserSteerPrompt(resumeHint), { streamingBehavior: "steer" }).catch(() => {});
      }
      return;
    }
    this.status = "driving";
    this.#driveStopped = false;
    this.lastStopReason = "";
    this.#driveEmptySettles = 0;
    this.#instantEmptySettles = 0;
    const started = Date.now();
    const deadline = started + timeoutMs;
    const idleMs = Math.max(20, Number(idleSubmitMs) || 90000);
    const emptyBudget = Math.max(1, Number(maxEmptySettles) || MAX_EMPTY_FINAL_SETTLES);
    const continueNow = (
      "继续用 control_in_app_browser 按 Skill 观察或最小操作。人也可以同时点预览。需要登录、写不进的字段或点了不发网的保存就 assist，不要锁预览。assist 之后必须停自动点，等用户说继续。不要盲点，不要 invent selector。"
    );
    const checkResult = typeof hasResult === "function" ? hasResult : null;
    let lastToolCount = this.#trace.toolCount;
    let lastToolAt = Date.now();
    let lastPromptAt = Date.now();
    let lastSeenTools = this.#trace.toolCount;
    let lastSeenTexts = Number(this.#trace.assistantTextCount) || 0;
    let abortResidueTurns = 0;
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
    this.#driveSettleOk = settleOk;
    this.#analysisSettleErr = settleErr;
    const resultReady = async () => {
      if (!checkResult) return false;
      try {
        return Boolean(await checkResult());
      } catch {
        return false;
      }
    };
    const scheduleRetry = (text) => {
      if (this.#driveStopped) {
        settleOk();
        return;
      }
      const toolsNow = this.#trace.toolCount;
      const textsNow = Number(this.#trace.assistantTextCount) || 0;
      const elapsedMs = Date.now() - lastPromptAt;
      const hadNewTools = toolsNow > lastSeenTools;
      const hadModelText = textsNow > lastSeenTexts;
      const abortResidue = isAbortResidueTurn({ interrupted, hadNewTools, hadModelText });
      const instant = isInstantEmptyTurn({ elapsedMs, hadNewTools, abortResidue });
      if (hadNewTools || hadModelText) {
        lastSeenTools = toolsNow;
        lastSeenTexts = textsNow;
        interrupted = false;
        abortResidueTurns = 0;
        this.#driveEmptySettles = 0;
        this.#instantEmptySettles = 0;
      } else if (abortResidue) {
        abortResidueTurns += 1;
        if (abortResidueTurns > 4) {
          this.lastStopReason = "instant_empty";
          logPiOnly("[PI操作] 中止后连续空轮，会话已失效，停止自动点击，人手通道继续");
          this.#emitThought({ kind: "text", text: "自动点击不可用，预览你继续点，或再发一句话让我继续" });
          this.#driveStopped = true;
          this.#abortLeftoverTurn();
          settleOk();
          return;
        }
        logPiOnly("[PI操作] 中止后的空轮不算空转，继续自动操作");
      } else if (instant) {
        this.#instantEmptySettles += 1;
        this.#driveEmptySettles += 1;
      } else {
        this.#instantEmptySettles = 0;
        this.#driveEmptySettles += 1;
      }
      if (this.#instantEmptySettles >= INSTANT_EMPTY_BUDGET || this.#driveEmptySettles >= emptyBudget) {
        this.lastStopReason = this.#instantEmptySettles >= INSTANT_EMPTY_BUDGET ? "instant_empty" : "empty";
        logPiOnly(`[PI操作] 连续 ${this.#driveEmptySettles} 轮空转，停止自动点击，人手通道继续`);
        this.#emitThought({ kind: "text", text: "自动点击不可用，预览你继续点，或再发一句话让我继续" });
        this.#driveStopped = true;
        this.#abortLeftoverTurn();
        settleOk();
        return;
      }
      logPiOnly("[PI操作] 本轮结束，继续自动点击");
      clearRetry();
      retryTimer = setTimeout(() => {
        retryTimer = null;
        if (settled || !this.alive) return;
        startPrompt(text);
      }, abortResidue ? RETRY_AFTER_ABORT_MS : instant ? RETRY_AFTER_INSTANT_MS : RETRY_AFTER_EMPTY_MS);
    };
    const interruptHungTurn = () => {
      if (settled || !this.alive || interrupted) return;
      interrupted = true;
      if (typeof this.session.abort !== "function") {
        lastToolAt = Date.now();
        return;
      }
      restartAfterAbort = true;
      this.#driveEmptySettles = 0;
      logPiOnly("[PI操作] 自动操作卡住，中止当前轮并继续");
      this.#emitThought({ kind: "text", text: "自动操作卡住，中止后继续点击" });
      Promise.resolve(this.session.abort())
        .catch(() => {})
        .finally(() => {
          if (settled || !this.alive) return;
          restartAfterAbort = false;
          if (this.#driveStopped) {
            settleOk();
            return;
          }
          startPrompt(continueNow);
        });
    };
    const onPromptSettled = async (error) => {
      if (settled) return;
      if (restartAfterAbort) return;
      if (!this.alive) {
        settleErr(new Error("PI 会话已关闭"));
        return;
      }
      if (this.#driveStopped || await resultReady()) {
        settleOk();
        return;
      }
      if (!checkResult) {
        if (error && !isAbortLikeError(error)) settleErr(error);
        else if (!error) settleOk();
        return;
      }
      if (isAbortLikeError(error)) return;
      if (error) {
        settleErr(error);
        return;
      }
      if (Date.now() >= deadline) {
        this.#emitThought({ kind: "text", text: "自动点击超时，预览你继续点" });
        this.lastStopReason = "timeout";
        this.#driveStopped = true;
        settleOk();
        return;
      }
      scheduleRetry(continueNow);
    };
    const startPrompt = (text, options) => {
      if (settled || !this.alive || this.#driveStopped) return;
      lastPromptAt = Date.now();
      let task;
      try {
        task = options ? this.session.prompt(text, options) : this.#promptFresh(text);
      } catch (error) {
        onPromptSettled(error);
        return;
      }
      Promise.resolve(task).then(
        (result) => {
          if (result?.queued) return;
          onPromptSettled();
        },
        (error) => onPromptSettled(error),
      );
    };
    logPiOnly(`[PI操作] 开始自动操作 timeout=${timeoutMs}ms`);
    if (!resumeHint) {
      this.#emitThought({ kind: "text", text: "PI 开始自动操作；预览你也可以点" });
    }
    startPrompt(resumeHint
      ? buildUserSteerPrompt(resumeHint)
      : buildLiveDrivePrompt({ targetUrl, goal }));
    const heartbeat = setInterval(() => {
      if (!this.alive) {
        settleErr(new Error("PI 会话已关闭"));
        return;
      }
      logPiOnly(`[PI操作] ${Math.round((Date.now() - started) / 1000)}s ${this.#trace.summary()}`);
    }, 15000);
    const resultWatch = checkResult
      ? setInterval(async () => {
        if (settled) return;
        if (this.#driveStopped || await resultReady()) settleOk();
      }, 250)
      : null;
    const idleWatch = setInterval(() => {
      if (settled || !this.alive || this.#driveStopped) return;
      if (this.#trace.toolCount !== lastToolCount) {
        lastToolCount = this.#trace.toolCount;
        lastToolAt = Date.now();
        steered = false;
        return;
      }
      const lastEventAt = Number(this.#trace.lastEventAt) || 0;
      if (lastEventAt > lastToolAt) {
        lastToolAt = lastEventAt;
        steered = false;
        return;
      }
      if (this.#trace.toolCount <= 0) return;
      const quietFor = Date.now() - lastToolAt;
      if (quietFor < idleMs) return;
      if (!steered) {
        steered = true;
        lastToolAt = Date.now();
        this.session.prompt(continueNow, { streamingBehavior: "steer" }).catch(() => {});
        return;
      }
      interruptHungTurn();
    }, Math.min(1000, Math.max(20, Math.floor(idleMs / 2) || 20)));
    let timeoutHandle;
    const timeoutTask = new Promise((_, reject) => {
      timeoutHandle = setTimeout(() => reject(new Error("PI 自动操作超时")), Math.max(50, timeoutMs));
    });
    try {
      await Promise.race([done, timeoutTask]);
    } catch (error) {
      this.lastError = error.message || String(error);
      this.lastStopReason = "timeout";
      this.#driveStopped = true;
      this.status = this.alive ? "ready" : "closed";
      this.#emitThought({ kind: "text", text: `自动点击不可用，预览你继续点：${this.lastError}` });
      return;
    } finally {
      this.#driveSettleOk = null;
      if (this.#analysisSettleErr === settleErr) this.#analysisSettleErr = null;
      clearRetry();
      clearTimeout(timeoutHandle);
      clearInterval(heartbeat);
      clearInterval(idleWatch);
      if (resultWatch) clearInterval(resultWatch);
    }
    if (this.#driveStopped) {
      this.status = "ready";
      return;
    }
    this.status = "submitted";
  }

  async stopLiveDrive() {
    this.#driveStopped = true;
    this.lastStopReason = this.lastStopReason || "finish";
    this.#emitThought({ kind: "text", text: "用户结束自动操作，准备提交已完成的能力" });
    try {
      await this.session.prompt(
        "用户结束。不要再 click。下一步只提交已经做完的能力，人点过的也要交。",
        { streamingBehavior: "steer" },
      );
    } catch {
      // 结束通知失败仍要让出自动操作循环
    }
    this.#driveSettleOk?.();
  }

  async requestFinalAnalysis({
    timeoutMs = 600000,
    idleSubmitMs = 90000,
    hasResult,
    hasDraft,
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
      "证据已经够了。不要把 JSON 写在对话里，不要再读证据。有真实 execute 形状的项用 submit_recording_capability 交；已有草稿就立刻 submit_recording_result({final:true, use_draft:true})。"
    );
    const checkResult = typeof hasResult === "function" ? hasResult : null;
    let lastToolCount = this.#trace.toolCount;
    let lastToolAt = Date.now();
    let lastPromptAt = Date.now();
    let lastSeenTools = this.#trace.toolCount;
    let lastSeenTexts = Number(this.#trace.assistantTextCount) || 0;
    let abortResidueTurns = 0;
    let emptySettles = 0;
    let instantEmptySettles = 0;
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
      const textsNow = Number(this.#trace.assistantTextCount) || 0;
      const elapsedMs = Date.now() - lastPromptAt;
      const hadNewTools = toolsNow > lastSeenTools;
      const hadModelText = textsNow > lastSeenTexts;
      const abortResidue = isAbortResidueTurn({ interrupted, hadNewTools, hadModelText });
      const instant = isInstantEmptyTurn({ elapsedMs, hadNewTools, abortResidue });
      if (hadNewTools || hadModelText) {
        lastSeenTools = toolsNow;
        lastSeenTexts = textsNow;
        interrupted = false;
        abortResidueTurns = 0;
        emptySettles = 0;
        instantEmptySettles = 0;
      } else if (abortResidue) {
        abortResidueTurns += 1;
        if (abortResidueTurns > 4) {
          this.lastStopReason = "instant_empty";
          logPiOnly("[PI分析] 中止后连续空轮未提交，停止重试");
          this.#emitThought({ kind: "text", text: "连续空转未提交，停止分析" });
          settleErr(new Error("transport_idle: 连续空转未调用工具且未提交"));
          return;
        }
        logPiOnly("[PI分析] 中止后的空轮不算空转，继续等待提交");
      } else if (instant) {
        instantEmptySettles += 1;
        emptySettles += 1;
      } else {
        instantEmptySettles = 0;
        emptySettles += 1;
      }
      if (instantEmptySettles >= INSTANT_EMPTY_BUDGET || emptySettles >= emptyBudget) {
        this.lastStopReason = instantEmptySettles >= INSTANT_EMPTY_BUDGET ? "instant_empty" : "empty";
        logPiOnly(`[PI分析] 连续 ${emptySettles} 轮空转未提交，停止重试`);
        this.#emitThought({ kind: "text", text: "连续空转未提交，停止分析" });
        settleErr(new Error("transport_idle: 连续空转未调用工具且未提交"));
        return;
      }
      logPiOnly("[PI分析] 本轮结束但未提交，继续要求 submit_recording_result");
      this.#emitThought({ kind: "text", text: "本轮结束但未提交，继续要求提交完整能力" });
      clearRetry();
      retryTimer = setTimeout(() => {
        retryTimer = null;
        if (settled || !this.alive) return;
        startPrompt(text);
      }, abortResidue ? RETRY_AFTER_ABORT_MS : instant ? RETRY_AFTER_INSTANT_MS : 0);
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
    const draftReady = typeof hasDraft === "function" && await hasDraft().catch(() => false);
    startPrompt(draftReady ? submitNow : buildFinalAnalysisPrompt(this.#latestSeq));
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
        return;
      }
      const lastEventAt = Number(this.#trace.lastEventAt) || 0;
      if (lastEventAt > lastToolAt) {
        lastToolAt = lastEventAt;
        steered = false;
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

  async beginSkillExport({
    title = "",
    timeoutMs = 900000,
    hasExport,
  } = {}) {
    if (!this.alive) throw new PiRequiredError("PI 会话已关闭");
    this.status = "exporting";
    const started = Date.now();
    const deadline = Date.now() + Math.max(30_000, Number(timeoutMs) || 900000);
    logPiOnly(`[出包] Skill4开始 title=${String(title || "本页办理").trim()} timeout_ms=${Math.max(30_000, Number(timeoutMs) || 900000)} session=${this.sessionId || "-"}`);
    const kick = (
      `你是 Build and Validate Dedicated Skill。不要点页面，不要交能力。\n` +
      `标题：${String(title || "本页办理").trim()}\n` +
      `运输层已按录制合同物化整包：CONTRACT、表单、路线、runtime.py、flow.py、client.py。\n` +
      `禁止重写 client/runtime/flow/CONTRACT/INPUT_FORMS，禁止另开子包，禁止发明 client.request。\n` +
      `1. read_generator_guides，读完返回的全部文件\n` +
      `2. read_export_contract，只认五块合同\n` +
      `3. read_skill_artifact("SKILL.md") 看运输层骨架；骨架不是成品\n` +
      `4. 按本 Skill 与 doc/ 覆盖 SKILL.md：完全基于能力。每个能力必须有冻结提问 JSON。调用方字段写成一次完整表单。系统常量必须写出实际合同值，由 runtime 自动填。无合同 default 的正文不编占位句。写操作日期可用 today。不准漏字段，禁止把能力字段改成系统后删掉\n` +
      `5. project_contract_to_request + validate_skill_package\n` +
      `6. 校验通过立刻 submit_skill_export({ok:true})，不要反复隔离跑\n` +
      `失败带 issues 调用 submit_skill_export({ok:false, errors:[...]})，不要假装发布。`
    );
    const nudge = "还没有 submit_skill_export。不要重写冻结执行器。校验通过立刻提交。";
    const ready = async () => {
      try {
        return Boolean(await hasExport?.());
      } catch {
        return false;
      }
    };
    logPiOnly(`[出包] Skill4准备发送启动指令 +${((Date.now() - started) / 1000).toFixed(1)}s`);
    await this.#promptNow(kick);
    logPiOnly(`[出包] Skill4启动指令已结束 +${((Date.now() - started) / 1000).toFixed(1)}s`);
    if (await ready()) {
      logPiOnly(`[出包] Skill4首轮已提交 +${((Date.now() - started) / 1000).toFixed(1)}s`);
      this.status = "submitted";
      return;
    }
    let nudgeCount = 0;
    while (Date.now() < deadline) {
      if (await ready()) {
        logPiOnly(`[出包] Skill4已提交 nudges=${nudgeCount} +${((Date.now() - started) / 1000).toFixed(1)}s`);
        this.status = "submitted";
        return;
      }
      nudgeCount += 1;
      logPiOnly(`[出包] Skill4催促第${nudgeCount}次 +${((Date.now() - started) / 1000).toFixed(1)}s`);
      await this.#promptNow(nudge);
    }
    if (await ready()) {
      logPiOnly(`[出包] Skill4截止前已提交 nudges=${nudgeCount} +${((Date.now() - started) / 1000).toFixed(1)}s`);
      this.status = "submitted";
      return;
    }
    logPiOnly(`[出包] Skill4超时 nudges=${nudgeCount} +${((Date.now() - started) / 1000).toFixed(1)}s`);
    throw new Error("Skill 4 出包超时");
  }

  async close() {
    const wasAlive = this.alive;
    this.alive = false;
    this.status = "closed";
    this.#driveSettleOk?.();
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
  const agentDir = recordingPiAgentDir();
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

  const loaded = await readRequiredSkills();
  const recordingSkills = loaded.filter((item) => RECORDING_SKILL_FILES.includes(item.name));
  const instructions = buildPiInstructions(recordingSkills);
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
        "list_action_timeline",
        "read_request_shape",
        "read_visible_controls",
        "read_evidence_delta",
        "read_evidence_item",
        "read_response_blob",
        "read_screenshot",
        "get_recording_freeze_state",
        "submit_recording_capability",
        "read_page_asset",
        "submit_recording_result",
        "control_in_app_browser",
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

export async function createExportPiSession({ recording, tools, onThought = null }) {
  assertNeverStartLegacy();
  logPiOnly(`[出包] Skill4会话初始化 recording=${recording?.id || "-"}`);
  const agentDir = exportPiAgentDir();
  const cwd = path.join(ROOT, "runtime", "pi-cwd", `${recording.id}`);
  await mkdir(agentDir, { recursive: true });
  await mkdir(cwd, { recursive: true });

  let createAgentSession;
  let SessionManager;
  let AuthStorage;
  let ModelRegistry;
  let DefaultResourceLoader;
  let defineTool;
  let Type;
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

  const authStorage = AuthStorage.create(path.join(agentDir, "auth.json"));
  const modelRegistry = ModelRegistry.create(authStorage, path.join(agentDir, "models.json"));
  const resolved = applyPiModelConfig(authStorage, modelRegistry);
  installOpenAIToolCallStreamCompatibility({
    baseUrl: resolved.baseUrl,
    onRepair: ({ toolCallCount }) => logPiOnly(`已补齐 OpenAI 兼容流的 finish_reason tool_calls=${toolCallCount}`),
  });

  const loaded = await readRequiredSkills();
  const skill4 = loaded.find((item) => item.name === EXPORT_SKILL_FILE);
  if (!skill4?.text) throw new Error("缺少 Skill 4");
  const instructions = buildExportPiInstructions(`## ${EXPORT_SKILL_FILE.replace(/\.md$/i, "")}\n\n${skill4.text}`);
  const trace = createPiTrace({ onThought });
  const customTools = wrapPiToolsForSdk(tools, defineTool, Type, trace, describeExportPiTools());
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir,
    systemPromptOverride: () => instructions,
  });
  if (typeof resourceLoader.reload === "function") {
    await resourceLoader.reload();
  }

  let created;
  try {
    created = await createAgentSession({
      cwd,
      agentDir,
      authStorage,
      modelRegistry,
      model: resolved.model,
      noTools: "builtin",
      tools: describeExportPiTools().map((item) => item.name),
      customTools,
      resourceLoader,
      sessionManager: SessionManager.inMemory(),
    });
  } catch (error) {
    logPiOnly(`[出包] Skill4会话初始化失败 ${error.message || error}`);
    throw new PiRequiredError(`出包 PI 初始化失败：${error.message}`, { cause: error });
  }

  const sessionId = String(created.session?.sessionId || `pi_export_${recording.id}`);
  logPiOnly(`[出包] Skill4会话就绪 session=${sessionId} model=${resolved.model?.id || resolved.model || "-"}`);
  return new LivePiSession({
    session: created.session,
    sessionId,
    dispose: () => created.session?.dispose?.(),
    instructions,
    onThought,
    trace,
  });
}
