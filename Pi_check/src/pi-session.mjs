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
import { HUMAN_STEER_MS, isUsefulAssistantThought } from "./browser-actions.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SKILL_PATH = path.join(ROOT, "skill", "RECORDING_CAPABILITY.md");
const BROWSER_SKILL_PATH = path.join(ROOT, "skill", "CONTROL_IN_APP_BROWSER.md");

export async function readRecordingSkill() {
  try {
    return await readFile(SKILL_PATH, "utf8");
  } catch {
    return "";
  }
}

export async function readControlInAppBrowserSkill() {
  try {
    return await readFile(BROWSER_SKILL_PATH, "utf8");
  } catch {
    return "";
  }
}

export function buildPiInstructions(skillText = "", browserSkillText = "") {
  const skill = String(skillText || "").trim();
  const browserSkill = String(browserSkillText || "").trim();
  return `${PI_ONLY_NOTICE}

你是本场调查的唯一语义权威。调查顺序、何时操作、何时交能力，以下面的 Skill 为准，不要另写一套点页面流程。
人同时也可以点预览。你们共用同一只浏览器、同一路画面、同一条证据。不要锁死预览。
最终必须产出能力。现有录制页会把你提交的 result 原样当作 draft 展示。
没有非空 capabilities，就等于没有产物。代码不会替你编造能力。

${skill}

${browserSkill ? `## Control In App Browser\n\n${browserSkill}\n` : ""}
可用工具：
- control_in_app_browser
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
- submit_recording_draft
- submit_recording_result

信封（录制页能读到才算交上，细节以 Skill 为准）：
- 不要写 capabilities[].fields。request_refs 必须是 {step_id, usage} 对象。steps[].params 必须是含 key/path 的对象数组。调用方字段必须出现在 input_schema.properties 或这些 params 里。
- 未接到用户结束禁止 submit_recording_result，只许 submit_recording_capability。
- submit_recording_result 必须包含 recording_id、final=true；完整 result 或 use_draft=true。
- 先用 list_action_timeline 建台账。每个独立业务动作都要有能力或 unresolved。capability_id 不得重复。每个能力恰好一个不共用的 execute。
- 系统会原样保存 result，不会补齐、改写或生成替代能力。
`;
}

export const PI_INSTRUCTIONS = buildPiInstructions(
  await readRecordingSkill(),
  await readControlInAppBrowserSkill(),
);

export function buildUserSteerPrompt(text, { finalizing = false } = {}) {
  const body = String(text || "").trim();
  if (finalizing) {
    return (
      `用户说：${body}\n` +
      `先用一两句话回答用户。不要再 click。已有草稿就立刻 submit_recording_result({final:true, use_draft:true})。没有完整能力就先按 Skill 交已有真实 execute 形状的项。`
    );
  }
  return (
    `用户说：${body}\n` +
    `这是对话。先用一两句话回答这句话，然后按 Skill 用 control_in_app_browser 继续当前页目标，不要盲点，不要 invent selector。人也可以点预览。不要锁预览。按目标做完的那次操作有真实 execute 形状再 submit_recording_capability。未接到用户结束，禁止 submit_recording_result。`
  );
}

export function buildLiveDrivePrompt({ targetUrl = "", goal = "" } = {}) {
  return (
    `你是操作者。人也同时可以点预览，共用这一页。调查顺序以 Skill 为准。\n` +
    `目标：${String(goal || "").trim() || "把该页独立业务动作做成可调用能力"}\n` +
    `入口：${String(targetUrl || "").trim()}\n` +
    `按 Skill 用 control_in_app_browser：open_page → snapshot → network_since。先观察，再按目标把当前动作的可见字段写上，再点该动作自己的查询或保存。不要盲点。\n` +
    `只用 snapshot 广告的 placeholder= / label= / role= / text= / ref=。禁止 name=、#id、CSS，禁止改点没有业务文案的 aN。fill 就写，写不进回 not_writable。choose 点可见原文；没有该项看打开后是列表还是日历。打开弹层后再 snapshot 一次。不要每个字段都 snapshot，不要 include_screenshot。screenshot 只回摘要，不要把图片写进对话。人点过的看 recentUserActions。\n` +
    `首屏自动请求不是已经做完。表单还是空的，不要点保存。没有搜索/查询文案时，点已经出现的树或列表节点。禁止点没文案的 aN。\n` +
    `readonly/disabled 只表示整个控件不能改。默认已选仍是调用方，不要写成无独立来源。\n` +
    `按目标做完的那次操作发出真实 execute 后再 submit_recording_capability。人点出的动作也要交。禁止交空壳。\n` +
    `登录、验证码、写不进的字段、点了不发网的保存：action=assist，预览不要锁。写入真实数据前若目标没授权，先 assist。协助之后不要再 click。\n` +
    `未接到用户结束，禁止 submit_recording_result。不要把 JSON 写在对话里。不要写 capabilities[].fields。`
  );
}

export function buildFinalAnalysisPrompt(latestSeq) {
  return (
    `证据已冻结，最新 seq=${Number(latestSeq) || 0}。现在必须产出能力。\n` +
    "先调 list_action_timeline 建台账，再用 list_recording_index 核对 interaction、xhr/fetch、network_response 和 visible_control。对候选 execute 调 read_request_shape；正文不够再 read_evidence_item。响应在 network_response 或读请求时附带的 response.body。\n" +
    "先读各页最近一次 visible_control（不要带弹层前旧 seq），再对 execute 每个 query/body 键。树/页签/分段器/单选组/日期区间都是可改选择。可改控件一律调用方；页面自动计算但仍可手工修改的输入也属于调用方。readonly/disabled 只表示整个控件不能改。默认已选仍是调用方。灰框才是系统，不要进 schema。分页只留 execute 系统栏，不准进 schema。每个 exposed_to_user=true 的 param 都必须出现在 schema，schema 顶层 key、param.key、param.path 的末级键必须逐字对应 execute 的真实 query/body 键，禁止相近拼写和别名。筛选项看得见但键看不清就 unresolved，禁止编 query 键。禁止编造写请求里没有的键。可增行只保留一个对象数组 key，禁止收成 string；items.properties title 用各分区表头原文，同键不同表头写 x-dano-section-titles。多分区必须 x-dano-section-titles，合并行时带分区标题。form textarea 不要用表格分区标题。确认弹层可填意见：有请求键就建模，没有就 unresolved，不要编新键。previous_response 必须写 from_step_id/from_path 并写成 links，不要把本场主键/单号当常量。option_source 只声明候选项来源，禁止把选项列表路径写成值流 links。登录身份用 current_user，不要写死本场数字。label/title 用页面原文，去掉星号。树单击是单值，schema type 必须和 param 一致，不要无证据写成 array。同一张表保存与提交若 path 或效果不同必须两项能力。\n" +
    "可改树/下拉/单选禁止只写 type=number。api_option 必须把 source_url 写进 param.source 和 schema 的 x-dano-option-source；page_enum 必须写当场全部 {label,value}。对象数组选择器的绑定只能写在对应 execute step.selects，禁止写到 result 顶层；必须包含 multi、label_subkey 和覆盖真实对象键的 element_template。把树/下拉藏在 description 里会被拒收。不要读 screenshot。\n" +
    "read_response_blob 只接受 body.blob_id（blob_ 开头）。不要把 request_id 当 blob_id。\n" +
    "该项已有真实 execute 形状再 submit_recording_capability。人点出的动作也要交。不要把 JSON 写在对话里。不要写 capabilities[].fields。request_refs 必须是 {step_id, usage}。steps[].params 必须是含 key/path 的对象数组。全部交完后 submit_recording_result({final:true, use_draft:true})。\n" +
    "若已有草稿，立刻 use_draft=true 提交。草稿不会自动变成结果。"
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
  return /连续空转未调用工具且未提交/.test(String(error?.message || error || ""));
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
      "继续用 control_in_app_browser 按 Skill 观察或最小操作。人也可以同时点预览。需要登录、写不进的字段或点了不发网的保存就 assist，不要锁预览。不要盲点，不要 invent selector。"
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
          settleErr(new Error("PI 连续空转未调用工具且未提交"));
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

  const instructions = buildPiInstructions(
    await readRecordingSkill(),
    await readControlInAppBrowserSkill(),
  );
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
        "submit_recording_draft",
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
