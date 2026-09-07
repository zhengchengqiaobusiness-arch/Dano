/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 唯一录制链路：先启动 PI，再开浏览器。PI 用 Control In App Browser 自动点，
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
    slot.assist = {
      reason: String(reason || "请在预览页帮忙：登录、验证码或确认写入"),
      at: new Date().toISOString(),
    };
    try {
      slot.onThought?.({ kind: "text", text: `请协助：${slot.assist.reason}。预览始终可以点。` });
    } catch {
      // ignore
    }
    try {
      slot.onAssist?.(this.view(recordingId));
    } catch {
      // ignore
    }
    return slot.assist;
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
      capabilityCount: session.status === "failed" || !session.hasFinalResult
        ? 0
        : this.#capabilityCountFromPiResultOnly(recordingId),
    };
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
      browserStartAttempted: false,
      assist: { reason: "" },
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
      const tools = createPiToolHost({
        recordingId: session.id,
        evidence: this.evidence,
        files: this.files,
        gate: this.gate,
        getPiSessionId: () => this.evidence.snapshot(session.id).piSessionId,
        getBrowser: () => this.#active.get(session.id)?.browser || null,
        freezeEvidence: async () => {
          if (!this.evidence.snapshot(session.id).frozen) {
            await this.evidence.freeze(session.id);
          }
        },
        onAssist: (payload) => this.requestAssist(session.id, payload?.reason || ""),
      });
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
      pi.onExit?.(() => {
        const current = this.evidence.snapshot(session.id);
        if (current.status === "succeeded" || current.hasFinalResult) return;
        this.#fail(session.id, "PI 在录制期间退出").catch(() => {});
      });

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
      });
      slot.browser = browser;
      await this.evidence.setStatus(session.id, {
        status: "recording",
        browserStatus: "recording",
        publicMessage: "PI 正在自动操作，预览你也可以点",
      });
      if (typeof pi.beginLiveDrive === "function") {
        slot.drive = Promise.resolve(pi.beginLiveDrive({
          targetUrl: session.targetUrl,
          goal: session.goal,
          timeoutMs: this.driveTimeoutMs,
          hasResult: () => this.files.hasPiResult(session.id),
        })).then(async () => {
          if (slot.failed || slot.completing) return;
          if (!await this.files.hasPiResult(session.id)) return;
          await this.#succeed(session.id);
        }).catch(async (error) => {
          if (slot.failed || slot.completing) return;
          logPiOnly(`自动点击不可用，人手点预览继续 recording=${session.id} ${error?.message || error}`);
          try {
            slot.onThought?.({
              kind: "text",
              text: "自动点击不可用，预览你继续点。结束后仍由 PI 根据证据产出能力。",
            });
          } catch {
            // 自动点击失败不得结束录制
          }
        });
      }
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
    try {
      await slot.pi.requestFinalAnalysis({
        timeoutMs: this.finalTimeoutMs,
        hasResult: () => this.files.hasPiResult(recordingId),
      });
    } catch (error) {
      const message = String(error.message || error);
      logPiOnly(`最终分析失败 recording=${recordingId} ${message}`);
      await this.#fail(recordingId, message);
      throw new RecordingFailedError(publicFailureMessage());
    }
    if (!await this.files.hasPiResult(recordingId)) {
      await this.#fail(recordingId, "停止录制后 PI 未提交最终结果");
      throw new RecordingFailedError(publicFailureMessage());
    }
    return this.#succeed(recordingId);
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
    try {
      await slot?.browser?.close();
    } catch {
      // ignore
    }
    try {
      await slot?.pi?.close({ reason: "completed" });
    } catch {
      // ignore
    }
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
    const event = await this.evidence.append(recordingId, kind, payload);
    const thought = thoughtFromEvidence(kind, payload);
    if (thought) {
      try {
        slot.onThought?.(thought);
      } catch {
        // 助手输出失败不得中断采集
      }
    }
    if (kind === "interaction" && payload?.actor !== "pi" && !/pointer|mousemove|mouseover/i.test(String(payload?.kind || ""))) {
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
