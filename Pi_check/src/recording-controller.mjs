/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 唯一录制链路：先启动 PI，再开浏览器。PI 按 Skill 观察和操作，
 * 人同时也可以点预览。两条通道共用同一页、同一路画面、同一条证据。
 * 没有第二条能力生成路径，异常处理中禁止生成替代结果。
 */

import {
  PI_ONLY_NOTICE,
  PiRequiredError,
  RecordingFailedError,
  assertNeverStartLegacy,
  logPiOnly,
  publicFailureMessage,
} from "./policy.mjs";
import { createPiToolHost } from "./pi-tools.mjs";
import { attachBlobSaver } from "./browser-capture.mjs";
import { capabilityCountFromPiResult } from "./capability-presence.mjs";
import { thoughtFromEvidence } from "./pi-trace.mjs";
import { resolveInteractionActor, shouldSteerHumanAct } from "./browser-actions.mjs";
import { isEmptySpinError, readRequiredSkills } from "./pi-session.mjs";
import path from "node:path";

export class RecordingController {
  constructor({
    files,
    evidence,
    gate,
    createPi,
    createBrowser,
    finalTimeoutMs = Number(process.env.PI_FINAL_TIMEOUT_MS || 600000),
    driveTimeoutMs = Number(process.env.PI_DRIVE_TIMEOUT_MS || process.env.PI_FINAL_TIMEOUT_MS || 600000),
  }) {
    assertNeverStartLegacy();
    this.files = files;
    this.evidence = evidence;
    this.gate = gate;
    this.createPi = createPi;
    this.createBrowser = createBrowser;
    this.finalTimeoutMs = finalTimeoutMs;
    this.driveTimeoutMs = driveTimeoutMs;
    this.#active = new Map();
    this.#lastCapabilityCount = new Map();
  }

  #active;
  #lastCapabilityCount;

  list() {
    return this.evidence.list();
  }

  browserOf(recordingId) {
    return this.#active.get(recordingId)?.browser || null;
  }

  acceptHumanInput(recordingId) {
    const session = this.evidence.snapshot(recordingId);
    if (!session || session.frozen || session.status === "failed" || session.status === "succeeded") {
      return false;
    }
    return Boolean(this.#active.get(recordingId)?.browser);
  }

  requestAssist(recordingId, reason = "") {
    const slot = this.#active.get(recordingId);
    if (!slot) return { reason: "" };
    slot.assistHold = true;
    slot.assist = {
      reason: String(reason || "请在预览页帮忙：登录、验证码或确认写入"),
      at: new Date().toISOString(),
    };
    try {
      slot.onThought?.({
        kind: "text",
        text: `已暂停自动操作。请协助：${slot.assist.reason}。预览始终可以点。做完后说「继续」。`,
      });
    } catch {
      // ignore
    }
    try {
      slot.onAssist?.(this.view(recordingId));
    } catch {
      // ignore
    }
    if (typeof slot.pi?.pauseForAssist === "function") {
      Promise.resolve(slot.pi.pauseForAssist(slot.assist.reason)).catch(() => {});
    }
    this.evidence.setStatus(recordingId, {
      publicMessage: "已暂停自动操作，请协助。预览始终可以点，做完后说继续。",
    }).catch(() => {});
    return slot.assist;
  }

  async steer(recordingId, text = "") {
    const slot = this.#active.get(recordingId);
    if (!slot?.pi?.alive) throw new Error("PI 会话未打开");
    const session = this.evidence.snapshot(recordingId);
    if (session.status === "failed" || session.status === "succeeded") {
      throw new Error("录制已结束，不能再发给 PI");
    }
    const needsFresh = slot.pi.needsFreshSession || slot.pi.lastStopReason === "instant_empty";
    const sent = await slot.pi.notifyUserMessage(text);
    if (!sent?.ok) throw new Error(sent?.error || "没有发给 PI");
    slot.assistHold = false;
    slot.assist = { reason: "" };
    if (sent.resumeDrive) {
      if (needsFresh) {
        try {
          await this.#replacePiSession(recordingId, "上一轮会话已失效，已新开 PI 按你的话继续");
        } catch (error) {
          logPiOnly(`重建 PI 失败，仍用原会话续跑 recording=${recordingId} ${error?.message || error}`);
        }
      }
      this.#launchDrive(recordingId, { resumeHint: sent.text || text });
      await this.evidence.setStatus(recordingId, {
        status: "recording",
        browserStatus: "recording",
        publicMessage: "PI 按你的话继续自动操作，预览你也可以点",
      }).catch(() => {});
    }
    return { ok: true, ...this.view(recordingId) };
  }

  async stopPiWork(recordingId) {
    const slot = this.#active.get(recordingId);
    if (!slot?.pi?.alive) throw new Error("PI 会话未打开");
    const session = this.evidence.snapshot(recordingId);
    if (session.status === "failed" || session.status === "succeeded") {
      throw new Error("录制已结束，没有可终止的任务");
    }
    if (typeof slot.pi.abortLiveWork === "function") {
      await slot.pi.abortLiveWork();
    } else if (typeof slot.pi.stopLiveDrive === "function") {
      await slot.pi.stopLiveDrive();
    }
    return { ok: true, ...this.view(recordingId) };
  }

  #launchDrive(recordingId, { resumeHint = "" } = {}) {
    const slot = this.#active.get(recordingId);
    const session = this.evidence.snapshot(recordingId);
    if (!slot?.pi || typeof slot.pi.beginLiveDrive !== "function") return;
    if (slot.pi.isDriving) return;
    slot.drive = Promise.resolve(slot.pi.beginLiveDrive({
      targetUrl: session.targetUrl,
      goal: session.goal,
      resumeHint,
      timeoutMs: this.driveTimeoutMs,
      hasResult: () => this.files.hasPiResult(recordingId),
    })).then(async () => {
      if (slot.failed || slot.completing) return;
      if (!await this.files.hasPiResult(recordingId)) return;
      await this.#succeed(recordingId);
    }).catch(async (error) => {
      if (slot.failed || slot.completing) return;
      logPiOnly(`自动点击不可用，人手点预览继续 recording=${recordingId} ${error?.message || error}`);
      try {
        slot.onThought?.({
          kind: "text",
          text: "自动点击不可用，预览你继续点，或在窗口里告诉 PI 接着做。结束后仍由 PI 根据证据产出能力。",
        });
      } catch {
        // 自动点击失败不得结束录制
      }
    });
  }

  #buildPiTools(recordingId) {
    return createPiToolHost({
      recordingId,
      evidence: this.evidence,
      files: this.files,
      gate: this.gate,
      getPiSessionId: () => this.evidence.snapshot(recordingId).piSessionId,
      getBrowser: () => this.#active.get(recordingId)?.browser || null,
      getTargetUrl: () => this.evidence.snapshot(recordingId).targetUrl || "",
      freezeEvidence: async () => {
        if (!this.evidence.snapshot(recordingId).frozen) {
          await this.evidence.freeze(recordingId);
        }
      },
      onAssist: (payload) => this.requestAssist(recordingId, payload?.reason || ""),
      isAssistHold: () => Boolean(this.#active.get(recordingId)?.assistHold),
      onPauseForAssist: (payload) => {
        const slot = this.#active.get(recordingId);
        slot?.pi?.pauseForAssist?.(payload?.reason || slot?.assist?.reason || "");
      },
      onFinalAccepted: () => this.#succeed(recordingId),
    });
  }

  #attachPiExit(recordingId, pi) {
    pi.onExit?.(() => {
      const slot = this.#active.get(recordingId);
      if (slot?.replacingPi || slot?.pi !== pi) return;
      const current = this.evidence.snapshot(recordingId);
      if (current.status === "succeeded" || current.hasFinalResult) return;
      this.#fail(recordingId, "PI 在录制期间退出").catch(() => {});
    });
  }

  #finalAnalysisOpts(recordingId) {
    return {
      timeoutMs: this.finalTimeoutMs,
      hasResult: () => this.files.hasPiResult(recordingId),
      hasDraft: async () => {
        const saved = await this.files.readDraft(recordingId).catch(() => null);
        return Array.isArray(saved?.draft?.capabilities) && saved.draft.capabilities.length > 0;
      },
    };
  }

  async #replacePiSession(recordingId, reason = "") {
    const slot = this.#active.get(recordingId);
    if (!slot) throw new Error("录制不存在");
    const session = this.evidence.snapshot(recordingId);
    if (session.status === "failed" || session.status === "succeeded") {
      throw new Error("录制已结束");
    }
    const tools = this.#buildPiTools(recordingId);
    logPiOnly(`重建 PI 会话 recording=${recordingId} ${reason}`);
    const next = await this.createPi({
      recording: session,
      tools,
      onThought: slot.onThought,
    });
    if (!next?.alive) throw new PiRequiredError("PI 无法重建");
    slot.replacingPi = true;
    const previous = slot.pi;
    slot.pi = next;
    this.#attachPiExit(recordingId, next);
    try {
      await previous?.close?.();
    } catch {
      // 旧会话关掉失败不得挡住新会话
    }
    slot.replacingPi = false;
    await this.evidence.setStatus(recordingId, {
      piStatus: "ready",
      piSessionId: next.sessionId,
      publicMessage: reason || "已新开 PI 会话继续",
    }).catch(() => {});
    try {
      slot.onThought?.({ kind: "text", text: reason || "已新开 PI 会话继续" });
    } catch {
      // 助手输出失败不得挡住新会话
    }
    return next;
  }

  async applySession(recordingId, session) {
    const browser = this.browserOf(recordingId);
    if (!browser) throw new Error("录制浏览器未启动");
    const applied = await browser.applySession(session || {});
    return { ...this.view(recordingId), pageUrl: applied?.url || "" };
  }

  async act(recordingId, command) {
    const browser = this.browserOf(recordingId);
    if (!browser) throw new Error("录制浏览器未启动");
    const applied = await browser.act(command || {});
    return {
      ...this.view(recordingId),
      pageUrl: applied?.url || "",
      results: applied?.results,
    };
  }

  snapshot(recordingId) {
    return this.view(recordingId);
  }

  view(recordingId) {
    const session = this.evidence.snapshot(recordingId);
    const slot = this.#active.get(recordingId);
    return {
      ...session,
      notice: PI_ONLY_NOTICE,
      human_can_click: this.acceptHumanInput(recordingId),
      assist: slot?.assist || { reason: "" },
      assist_paused: Boolean(slot?.assistHold),
      capabilityCount: session.status === "failed" || !session.hasFinalResult
        ? 0
        : this.#capabilityCountFromPiResultOnly(recordingId),
    };
  }

  reattach(recordingId, hooks = {}) {
    const slot = this.#active.get(recordingId);
    if (!slot) throw new Error("录制不存在");
    const session = this.evidence.snapshot(recordingId);
    if (session.status === "failed" || session.status === "succeeded") {
      throw new Error("录制已结束");
    }
    if (typeof hooks.onThought === "function") slot.onThought = hooks.onThought;
    if (typeof hooks.onAssist === "function") slot.onAssist = hooks.onAssist;
    if (typeof hooks.onFailed === "function") slot.onFailed = hooks.onFailed;
    if (typeof hooks.onComplete === "function") slot.onComplete = hooks.onComplete;
    return this.view(recordingId);
  }

  #capabilityCountFromPiResultOnly(recordingId) {
    if (!this.gate.accepted.has(recordingId)) return 0;
    return this.#lastCapabilityCount.get(recordingId) ?? 0;
  }

  async start({
    targetUrl,
    goal,
    storageState = null,
    title = "",
    action = "",
    viewport = null,
    onThought = null,
    onComplete = null,
    onAssist = null,
    onFailed = null,
  }) {
    assertNeverStartLegacy();
    await readRequiredSkills();
    if (!String(targetUrl || "").trim()) throw new Error("必须提供目标页面地址");
    if (!String(goal || "").trim()) throw new Error("必须提供录制目标");

    const session = await this.evidence.create({
      targetUrl: String(targetUrl).trim(),
      goal: String(goal).trim(),
      title: String(title || "").trim(),
      action: String(action || "").trim(),
    });
    const slot = {
      id: session.id,
      pi: null,
      browser: null,
      failed: false,
      completing: false,
      teardownScheduled: false,
      replacingPi: false,
      analysisRetried: false,
      browserStartAttempted: false,
      assist: { reason: "" },
      assistHold: false,
      drive: null,
      onThought: typeof onThought === "function" ? onThought : null,
      onComplete: typeof onComplete === "function" ? onComplete : null,
      onAssist: typeof onAssist === "function" ? onAssist : null,
      onFailed: typeof onFailed === "function" ? onFailed : null,
    };
    this.#active.set(session.id, slot);

    try {
      logPiOnly(`正在启动 PI 会话 recording=${session.id}`);
      await this.evidence.setStatus(session.id, {
        status: "starting_pi",
        piStatus: "starting",
        browserStatus: "idle",
        publicMessage: "正在启动 PI 会话",
      });
      const tools = this.#buildPiTools(session.id);
      const pi = await this.createPi({ recording: session, tools, onThought: slot.onThought });
      if (!pi || !pi.alive) {
        throw new PiRequiredError("PI 无法启动");
      }
      slot.pi = pi;
      logPiOnly(`PI 会话已就绪 session=${pi.sessionId}`);
      await this.evidence.setStatus(session.id, {
        piStatus: "ready",
        piSessionId: pi.sessionId,
        publicMessage: "PI 已启动，正在打开浏览器",
      });
      this.#attachPiExit(session.id, pi);

      slot.browserStartAttempted = true;
      await this.evidence.setStatus(session.id, {
        status: "starting_browser",
        browserStatus: "starting",
      });
      const appendEvidence = attachBlobSaver(
        async (kind, payload) => this.#append(session.id, kind, payload),
        this.evidence,
        session.id,
      );
      const browser = await this.createBrowser({
        recording: {
          ...this.evidence.snapshot(session.id),
          storageState,
          viewport,
        },
        appendEvidence,
        authVaultPath: path.join(this.files.directory(session.id), "auth-vault.json"),
      });
      slot.browser = browser;
      await this.evidence.setStatus(session.id, {
        status: "recording",
        browserStatus: "recording",
        publicMessage: "PI 正在自动操作，预览你也可以点",
      });
      this.#launchDrive(session.id);
      return this.view(session.id);
    } catch (error) {
      await this.#fail(session.id, error.message || String(error));
      throw error instanceof PiRequiredError || error instanceof RecordingFailedError
        ? error
        : new PiRequiredError(error.message || String(error), { cause: error });
    }
  }

  async stop(recordingId) {
    const slot = this.#active.get(recordingId);
    const session = this.evidence.snapshot(recordingId);
    if (session.status === "succeeded" && session.hasFinalResult) {
      return this.#succeed(recordingId);
    }
    if (await this.files.hasPiResult(recordingId)) {
      if (!session.frozen) {
        await this.evidence.freeze(recordingId);
      }
      return this.#succeed(recordingId);
    }
    if (!slot?.pi?.alive) {
      await this.#fail(recordingId, "PI 在录制期间退出");
      throw new RecordingFailedError(publicFailureMessage());
    }
    if (session.status === "failed") {
      throw new RecordingFailedError(publicFailureMessage());
    }
    try {
      await slot.pi.stopLiveDrive?.();
    } catch {
      // 结束自动操作失败仍要冻结并定稿
    }
    if (!this.evidence.snapshot(recordingId).frozen) {
      await this.evidence.freeze(recordingId);
    }
    if (await this.files.hasPiResult(recordingId)) {
      return this.#succeed(recordingId);
    }
    logPiOnly("自动操作已结束，浏览器保持打开，等待 PI 提交已完成的能力");
    await this.evidence.setStatus(recordingId, {
      status: "pi_finalizing",
      browserStatus: "inspecting",
      piStatus: "finalizing",
      publicMessage: "自动操作已结束，等待 PI 提交能力",
    });
    const analysisOpts = this.#finalAnalysisOpts(recordingId);
    try {
      await slot.pi.requestFinalAnalysis(analysisOpts);
    } catch (error) {
      const message = String(error.message || error);
      if (isEmptySpinError(error) && !slot.analysisRetried) {
        slot.analysisRetried = true;
        logPiOnly(`最终分析空转，重建 PI 再试一次 recording=${recordingId}`);
        try {
          await this.#replacePiSession(recordingId, "最终分析空转，已新开 PI 继续提交");
          await slot.pi.requestFinalAnalysis(analysisOpts);
        } catch (retryError) {
          const retryMessage = String(retryError.message || retryError);
          logPiOnly(`最终分析重试失败 recording=${recordingId} ${retryMessage}`);
          await this.#fail(recordingId, retryMessage);
          throw new RecordingFailedError(publicFailureMessage());
        }
      } else {
        logPiOnly(`最终分析失败 recording=${recordingId} ${message}`);
        await this.#fail(recordingId, message);
        throw new RecordingFailedError(publicFailureMessage());
      }
    }
    if (!await this.files.hasPiResult(recordingId)) {
      await this.#fail(recordingId, "停止录制后 PI 未提交最终结果");
      throw new RecordingFailedError(publicFailureMessage());
    }
    return this.#succeed(recordingId);
  }

  #scheduleSiteTeardown(recordingId) {
    const slot = this.#active.get(recordingId);
    if (!slot || slot.teardownScheduled) return;
    slot.teardownScheduled = true;
    const tearDown = async () => {
      try {
        await slot.browser?.close();
      } catch {
        // ignore
      }
      try {
        await slot.pi?.close({ reason: "completed" });
      } catch {
        // ignore
      }
    };
    setImmediate(() => {
      tearDown().catch(() => {});
    });
  }

  async #succeed(recordingId) {
    const slot = this.#active.get(recordingId);
    if (slot?.completing) {
      return {
        session: this.view(recordingId),
        result: await this.files.readPiResult(recordingId),
        receipt: await this.files.readReceipt(recordingId),
      };
    }
    if (slot) slot.completing = true;
    const result = await this.files.readPiResult(recordingId);
    const capabilityCount = capabilityCountFromPiResult(result);
    if (!capabilityCount) {
      await this.files.deletePiResult(recordingId);
      if (slot) slot.completing = false;
      await this.#fail(recordingId, "PI 未提交任何能力，没有产出");
      throw new RecordingFailedError(publicFailureMessage());
    }
    this.#lastCapabilityCount.set(recordingId, capabilityCount);
    await this.evidence.setStatus(recordingId, {
      status: "succeeded",
      piStatus: "submitted",
      browserStatus: "stopped",
      hasFinalResult: true,
      publicMessage: `PI 已提交 ${capabilityCount} 项能力`,
    });
    const payload = {
      session: this.view(recordingId),
      result: await this.files.readPiResult(recordingId),
      receipt: await this.files.readReceipt(recordingId),
    };
    try {
      await slot?.onComplete?.(payload);
    } catch {
      // 完成通知失败不得改写结果
    }
    this.#scheduleSiteTeardown(recordingId);
    return payload;
  }

  async cancel(recordingId) {
    await this.#fail(recordingId, "PI 会话被取消");
    throw new RecordingFailedError(publicFailureMessage());
  }

  async result(recordingId) {
    const session = this.view(recordingId);
    if (session.status !== "succeeded" || !session.hasFinalResult) {
      return {
        session,
        result: null,
        receipt: null,
        capabilityCount: 0,
        publicMessage: publicFailureMessage(),
      };
    }
    return {
      session,
      result: await this.files.readPiResult(recordingId),
      receipt: await this.files.readReceipt(recordingId),
    };
  }

  async #append(recordingId, kind, payload) {
    const slot = this.#active.get(recordingId);
    const session = this.evidence.snapshot(recordingId);
    if (session.frozen || session.status === "failed" || session.status === "succeeded") {
      return null;
    }
    if (!slot?.pi?.alive) {
      await this.#fail(recordingId, "PI 在录制期间退出");
      return null;
    }
    const nextPayload = kind === "interaction"
      ? { ...payload, actor: resolveInteractionActor(slot.browser, payload) }
      : payload;
    const event = await this.evidence.append(recordingId, kind, nextPayload);
    const thought = thoughtFromEvidence(kind, nextPayload);
    if (thought) {
      try {
        slot.onThought?.(thought);
      } catch {
        // 助手输出失败不得中断采集
      }
    }
    if (shouldSteerHumanAct(kind, nextPayload)) {
      Promise.resolve(slot.pi.notifyHumanAct?.({ seq: event.seq })).catch(() => {});
    }
    if (shouldNotifyPi(kind, payload)) {
      Promise.resolve(slot.pi.notifyEvidence({ seq: event.seq }))
        .catch(async (error) => {
          await this.#fail(recordingId, `PI 工具调用失败且未恢复：${error.message || error}`);
        });
    }
    return event;
  }

  async #fail(recordingId, message) {
    const slot = this.#active.get(recordingId);
    if (slot?.failed) return;
    if (slot) slot.failed = true;
    let current = null;
    try {
      current = this.evidence.snapshot(recordingId);
    } catch {
      return;
    }
    if (current.status === "succeeded" || current.hasFinalResult) return;
    try {
      await slot?.browser?.close();
    } catch {
      // ignore
    }
    try {
      await slot?.pi?.close({ reason: "failed" });
    } catch {
      // ignore
    }
    await this.files.deletePiResult(recordingId);
    await this.evidence.setStatus(recordingId, {
      status: "failed",
      piStatus: current.piStatus === "starting" ? "failed" : "failed",
      browserStatus: slot?.browserStartAttempted ? "stopped" : "idle",
      error: message,
      hasFinalResult: false,
      publicMessage: publicFailureMessage(),
    });
    try {
      await slot?.onFailed?.(this.view(recordingId));
    } catch {
      // 失败通知不得编造能力
    }
  }
}

function shouldNotifyPi(kind, payload) {
  if (kind === "interaction") return true;
  if (kind === "network_request") {
    const type = String(payload?.resource_type || "");
    return type === "xhr" || type === "fetch";
  }
  return false;
}

export function displayedCapabilityCount(result, session) {
  if (!session || session.status !== "succeeded" || !session.hasFinalResult || !result) {
    return 0;
  }
  return capabilityCountFromPiResult(result);
}
