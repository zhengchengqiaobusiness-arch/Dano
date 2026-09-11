/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { RecordingFiles } from "../../src/fs-store.mjs";
import { EvidenceStore } from "../../src/evidence-store.mjs";
import { ResultGate } from "../../src/result-gate.mjs";
import { RecordingController } from "../../src/recording-controller.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

export function sampleResult(overrides = {}) {
  return {
    recording_goal: "演示目标",
    business: { object: "leave", intent: "create" },
    capabilities: [
      {
        capability_id: "cap_create_leave",
        name: "create_leave",
        title: "创建请假",
        intent: "创建请假",
        kind: "write",
        request_refs: [{ step_id: "step_1", usage: "execute" }],
      },
    ],
    steps: [
      {
        step_id: "step_1",
        method: "POST",
        path: "/api/leave",
        params: [
          {
            key: "days",
            path: "body.days",
            label: "天数",
            type: "number",
            source_kind: "caller_input",
            required: true,
            exposed_to_user: true,
          },
        ],
      },
    ],
    links: [],
    request_order: ["req_1"],
    dependencies: [],
    success_condition: "status==200",
    failure_condition: "status>=400",
    evidence_refs: [1],
    unresolved: [],
    ...overrides,
  };
}

export class ScriptedPiSession {
  constructor({
    tools,
    recordingId,
    sessionId = "scripted-pi",
    behavior = "submit_on_final",
    result,
    delayMs = 250,
    onThought = null,
  }) {
    this.tools = tools;
    this.recordingId = recordingId;
    this.sessionId = sessionId;
    this.behavior = behavior;
    this.result = result || sampleResult();
    this.delayMs = Number(delayMs) || 250;
    this.alive = true;
    this.started = true;
    this.status = "ready";
    this.unfrozenRejected = false;
    this.secondSubmitError = "";
    this.humanActs = 0;
    this.userMessages = [];
    this.driveStarted = false;
    this.driveStopped = false;
    this.lastStopReason = "";
    this.aborted = false;
    this.onThought = typeof onThought === "function" ? onThought : null;
    this.exitListeners = new Set();
    this.toolInFlight = 0;
    this.closeDuringTool = false;
    this.submitReturned = false;
  }

  async #runTool(name, args) {
    this.toolInFlight += 1;
    try {
      const out = await this.tools[name](args);
      if (name === "submit_recording_result") this.submitReturned = true;
      return out;
    } finally {
      this.toolInFlight -= 1;
    }
  }

  async callSubmit(result) {
    return this.#runTool("submit_recording_result", {
      recording_id: this.recordingId,
      final: true,
      result: result || this.result,
    });
  }

  onExit(listener) {
    this.exitListeners.add(listener);
  }

  kill() {
    this.alive = false;
    for (const listener of this.exitListeners) listener();
  }

  async notifyEvidence() {
    if (!this.alive) return;
    if (this.behavior === "submit_unfrozen") {
      try {
        await this.#runTool("submit_recording_result", {
          recording_id: this.recordingId,
          final: true,
          result: this.result,
        });
      } catch {
        this.unfrozenRejected = true;
      }
    }
    if (this.behavior === "die_on_notify") {
      this.kill();
    }
  }

  async notifyHumanAct() {
    this.humanActs += 1;
  }

  async notifyUserMessage(text = "") {
    const message = String(text || "").trim();
    if (!message) return { ok: false, error: "请输入要发给 PI 的话" };
    this.userMessages.push(message);
    try {
      this.onThought?.({ kind: "user", text: message });
    } catch {
      // 助手输出失败不得挡住发给 PI
    }
    const resumeDrive = this.driveStopped || this.status !== "driving";
    this.driveStopped = false;
    this.status = "driving";
    return { ok: true, resumeDrive, text: message };
  }

  get needsFreshSession() {
    return this.lastStopReason === "instant_empty";
  }

  async abortLiveWork() {
    this.aborted = true;
    this.driveStopped = true;
    this.lastStopReason = "user";
    if (this.status === "driving") this.status = "ready";
    return { ok: true };
  }

  async pauseForAssist(reason = "") {
    this.aborted = true;
    this.driveStopped = true;
    this.lastStopReason = "assist";
    if (this.status === "driving") this.status = "ready";
    try {
      this.onThought?.({
        kind: "text",
        text: `已暂停自动操作。${String(reason || "请在预览协助")}。做完后说继续。`,
      });
    } catch {
      // 协助提示失败仍要停自动点
    }
    return { ok: true };
  }

  async beginLiveDrive({ resumeHint = "" } = {}) {
    this.driveStarted = true;
    this.driveStopped = false;
    this.lastStopReason = "";
    this.status = "driving";
    if (resumeHint) this.userMessages.push(String(resumeHint));
    if (this.behavior === "drive_fail") {
      throw new Error("PI 连续空转未自动操作");
    }
    if (this.behavior === "submit_on_drive") {
      await this.#runTool("submit_recording_result", {
        recording_id: this.recordingId,
        final: true,
        result: this.result,
      });
    }
  }

  async stopLiveDrive() {
    this.driveStopped = true;
    this.status = this.status === "driving" ? "ready" : this.status;
  }

  async requestFinalAnalysis() {
    if (!this.alive) throw new Error("PI 在录制期间退出");
    if (this.behavior === "empty_spin") {
      throw new Error("PI 连续空转未调用工具且未提交");
    }
    if (this.behavior === "never_submit" || this.behavior === "submit_unfrozen") return;
    if (this.behavior === "submit_after_delay") {
      await new Promise((resolve) => setTimeout(resolve, this.delayMs));
      if (!this.alive) throw new Error("PI 会话已关闭");
      await this.#runTool("submit_recording_result", {
        recording_id: this.recordingId,
        final: true,
        result: this.result,
      });
      return;
    }
    if (this.behavior === "empty_result") {
      await this.#runTool("submit_recording_result", {
        recording_id: this.recordingId,
        final: true,
        result: {},
      });
      return;
    }
    if (this.behavior === "wrong_id") {
      await this.#runTool("submit_recording_result", {
        recording_id: "rec_does_not_match",
        final: true,
        result: this.result,
      });
      return;
    }
    if (this.behavior === "not_final") {
      await this.#runTool("submit_recording_result", {
        recording_id: this.recordingId,
        final: false,
        result: this.result,
      });
      return;
    }
    await this.#runTool("submit_recording_result", {
      recording_id: this.recordingId,
      final: true,
      result: this.result,
    });
    if (this.behavior === "submit_twice") {
      try {
        await this.#runTool("submit_recording_result", {
          recording_id: this.recordingId,
          final: true,
          result: { ...this.result, injected_by_second_submit: true },
        });
      } catch (error) {
        this.secondSubmitError = error.message;
      }
    }
  }

  async close() {
    this.closeDuringTool = this.toolInFlight > 0;
    this.alive = false;
  }
}

export class FakeBrowser {
  constructor({ appendEvidence, emitOnStart = true } = {}) {
    this.started = true;
    this.closed = false;
    this.appendEvidence = appendEvidence;
    this.inputs = [];
    this.acts = [];
    this.lastHumanActAt = 0;
    this.piActDepth = 0;
    this.piActUntil = 0;
    this.userActions = [];
    this.ready = emitOnStart
      ? Promise.resolve(appendEvidence?.("network_request", {
        method: "GET",
        url: "http://fixture.local/demo",
        resource_type: "xhr",
      }))
      : Promise.resolve();
  }

  recentlyHuman(ms = 800) {
    return Date.now() - Number(this.lastHumanActAt || 0) < ms;
  }

  beginPiAct() {
    this.piActDepth += 1;
  }

  endPiAct() {
    this.piActDepth = Math.max(0, this.piActDepth - 1);
    this.piActUntil = Date.now() + 400;
  }

  isPiActing() {
    return this.piActDepth > 0 || Date.now() < this.piActUntil;
  }

  recentUserActions() {
    return this.userActions || [];
  }

  async applyInput(event) {
    this.inputs.push(event || {});
    const kind = String(event?.kind || "click");
    if (!/pointer_move|mousemove/i.test(kind)) {
      this.lastHumanActAt = Date.now();
      this.userActions = [...(this.userActions || []), { kind, at: Date.now() }].slice(-12);
    }
    if (typeof this.appendEvidence === "function" && !/pointer_move|mousemove/i.test(kind)) {
      await this.appendEvidence("interaction", {
        actor: "human",
        kind,
      });
    }
  }

  async inspect({ includeScreenshot = false } = {}) {
    return {
      available: true,
      url: "http://fixture.local/demo",
      controls: [{ ref: "c1", label: "关键字", selector: "placeholder=请输入单据编号", placeholder: "请输入单据编号" }],
      actions: [{ ref: "a1", label: "查询", selector: 'role=button[name="查询"]' }],
      recentUserActions: this.userActions || [],
      ...(includeScreenshot ? { screenshot: { data: "AAAA", width: 10, height: 10 } } : {}),
    };
  }

  async actByRef({ ref = "", action = "click", text = "", selector = "" } = {}) {
    return this.actBySelector({ selector: selector || ref, ref: ref || selector, action, text });
  }

  async actBySelector({ selector = "", ref = "", action = "click", text = "" } = {}) {
    const token = selector || ref;
    this.beginPiAct();
    try {
      this.acts.push({ ref: token, selector: token, action, text });
      if (typeof this.appendEvidence === "function") {
        await this.appendEvidence("interaction", {
          actor: "pi",
          kind: action || "click",
          ref: token,
          selector: token,
          text,
        });
      }
      return { ok: true, url: "http://fixture.local/demo", ref: token, selector: token, action };
    } finally {
      this.endPiAct();
    }
  }

  async openPage(url) {
    return { available: true, url: String(url || "http://fixture.local/demo") };
  }

  async listPages() {
    return { pages: [{ url: "http://fixture.local/demo", current: true }] };
  }

  async fillFields(fields = []) {
    const results = [];
    for (const field of fields) {
      results.push(await this.actByRef({
        ref: field?.ref,
        action: "fill",
        text: field?.value ?? field?.text ?? "",
      }));
    }
    return { ok: results.every((item) => item.ok), filled: results.filter((item) => item.ok).length, results };
  }

  async close() {
    this.closed = true;
    this.started = false;
  }
}

async function rmRetry(dir) {
  let lastError;
  for (let i = 0; i < 6; i += 1) {
    try {
      await rm(dir, { recursive: true, force: true });
      return;
    } catch (error) {
      lastError = error;
      if (error?.code !== "ENOTEMPTY" && error?.code !== "EBUSY" && error?.code !== "EPERM") {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 40 * (i + 1)));
    }
  }
  if (lastError) throw lastError;
}

export async function createHarness(options = {}) {
  const dir = await mkdtemp(path.join(ROOT, "data", "tmp-"));
  const files = new RecordingFiles(dir);
  const evidence = new EvidenceStore(files);
  const gate = new ResultGate(files);
  const browserCalls = [];
  const piBehavior = options.piBehavior || "submit_on_final";
  const result = options.result;
  let piRef = null;
  let piCreateCount = 0;
  const controller = new RecordingController({
    files,
    evidence,
    gate,
    finalTimeoutMs: options.finalTimeoutMs || 2000,
    driveTimeoutMs: options.driveTimeoutMs || 2000,
    createPi: options.createPi || (async ({ recording, tools, onThought }) => {
      if (options.piFailStart) {
        throw new Error("PI 初始化失败");
      }
      piCreateCount += 1;
      const behavior = typeof options.piBehaviorForCreate === "function"
        ? options.piBehaviorForCreate(piCreateCount)
        : piBehavior;
      piRef = new ScriptedPiSession({
        tools,
        recordingId: recording.id,
        behavior,
        result,
        sessionId: options.piSessionId || (piCreateCount === 1 ? "scripted-pi" : `scripted-pi-${piCreateCount}`),
        delayMs: options.piDelayMs,
        onThought,
      });
      return piRef;
    }),
    createBrowser: options.createBrowser || (async ({ appendEvidence }) => {
      browserCalls.push(true);
      const browser = new FakeBrowser({ appendEvidence, emitOnStart: options.emitOnStart !== false });
      await browser.ready;
      return browser;
    }),
  });
  return {
    root: ROOT,
    dir,
    files,
    evidence,
    gate,
    controller,
    browserCalls,
    getPi: () => piRef,
    getPiCreateCount: () => piCreateCount,
    async cleanup() {
      try {
        await controller.browserOf?.(evidence.list()?.[0]?.id)?.close?.();
      } catch {
        // ignore
      }
      await rmRetry(dir);
    },
  };
}
