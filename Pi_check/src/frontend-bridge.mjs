/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 对接现有 PageRecorder：预览尺寸由页面送来，采集按该视口截帧，避免拉伸。
 * 人点预览始终落到同一只浏览器；PI 自动点也走同一路画面。
 * draft 只来自 PI 最终提交；没有能力就不能成功。
 */

import { WebSocketServer } from "ws";
import { PI_ONLY_NOTICE, logPiOnly, publicFailureMessage } from "./policy.mjs";
import { capabilityCountFromPiResult } from "./capability-presence.mjs";
import { shouldFlushFrame } from "./browser-capture.mjs";

function send(socket, payload) {
  if (socket.readyState === 1) {
    socket.send(JSON.stringify(payload));
  }
}

function newAction() {
  return `action_${crypto.randomUUID().replaceAll("-", "")}`;
}

export const FRONTEND_DISCONNECT_GRACE_MS = 20000;
const pendingDisconnectCancels = new Map();

function isAssistHoldView(view) {
  return Boolean(view?.assist_paused || String(view?.assist?.reason || "").trim());
}

export function shouldCancelOnFrontendDisconnect(view, { finalizing = false } = {}) {
  if (finalizing) return false;
  if (!view) return false;
  if (view.status === "succeeded" || view.hasFinalResult) return false;
  if (view.frozen || view.status === "pi_finalizing") return false;
  if (isAssistHoldView(view)) return false;
  return true;
}

export function clearDisconnectCancel(recordingId) {
  const key = String(recordingId || "");
  const timer = pendingDisconnectCancels.get(key);
  if (!timer) return;
  clearTimeout(timer);
  pendingDisconnectCancels.delete(key);
}

export function scheduleDisconnectCancel(controller, recordingId, ms = FRONTEND_DISCONNECT_GRACE_MS) {
  const key = String(recordingId || "");
  if (!key) return;
  clearDisconnectCancel(key);
  const timer = setTimeout(() => {
    pendingDisconnectCancels.delete(key);
    logPiOnly(`前台断开超时，结束这场录制 recording=${key}`);
    controller.cancel(key).catch(() => {});
  }, Math.max(20, Number(ms) || FRONTEND_DISCONNECT_GRACE_MS));
  pendingDisconnectCancels.set(key, timer);
}

export function attachFrontendBridge(httpServer, { controller, catalog, disconnectGraceMs = FRONTEND_DISCONNECT_GRACE_MS } = {}) {
  const wss = new WebSocketServer({ noServer: true });
  httpServer.on("upgrade", (req, socket, head) => {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    if (url.pathname !== "/onboarding/page/record") {
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req));
  });

  wss.on("connection", (ws) => {
    const session = {
      revision: 0,
      action: "",
      recordingId: "",
      title: "",
      finalizing: false,
      closed: false,
      published: false,
      started: Promise.resolve(),
      frameSeq: 0,
      frames: null,
      frameBusy: false,
      frameWanted: false,
      flushTimer: null,
      inputChain: Promise.resolve(),
    };

    const viewExtra = () => {
      if (!session.recordingId) return {};
      try {
        const view = controller.view(session.recordingId);
        return {
          assist: view.assist || { reason: "" },
          human_can_click: view.human_can_click !== false,
        };
      } catch {
        return {};
      }
    };

    const snapshot = (status, extra = {}) => {
      session.revision += 1;
      return {
        type: "snapshot",
        snapshot: {
          run_id: session.recordingId || session.action,
          action: session.action,
          title: session.title,
          revision: session.revision,
          status,
          progress: extra.progress || { step: status, label: extra.label || status },
          capture_frozen: Boolean(extra.capture_frozen),
          draft: extra.draft ?? null,
          draft_fingerprint: extra.draft_fingerprint,
          error: extra.error || "",
          notice: PI_ONLY_NOTICE,
          assist: extra.assist ?? viewExtra().assist ?? { reason: "" },
          human_can_click: extra.human_can_click ?? viewExtra().human_can_click ?? true,
        },
      };
    };

    const think = (payload) => {
      const thought = typeof payload === "string"
        ? { kind: "text", text: payload }
        : (payload && typeof payload === "object" ? payload : null);
      if (!thought) return;
      if (!thought.text && thought.kind !== "tool") return;
      send(ws, { type: "thought", ...thought });
    };

    const stopFrames = () => {
      if (session.frames) {
        clearInterval(session.frames);
        session.frames = null;
      }
      if (session.flushTimer != null) {
        clearTimeout(session.flushTimer);
        session.flushTimer = null;
      }
    };

    const emitFrame = async (browser) => {
      if (session.frameBusy) {
        session.frameWanted = true;
        return;
      }
      session.frameBusy = true;
      try {
        do {
          session.frameWanted = false;
          const frame = await browser?.captureFrame?.();
          if (!frame) continue;
          session.frameSeq += 1;
          send(ws, {
            type: "frame",
            seq: session.frameSeq,
            data: frame.data,
            width: frame.width,
            height: frame.height,
          });
        } while (session.frameWanted);
      } catch {
        // 截帧失败不得编造能力
      } finally {
        session.frameBusy = false;
      }
    };

    const scheduleFlush = (browser) => {
      if (session.flushTimer != null) clearTimeout(session.flushTimer);
      session.flushTimer = setTimeout(() => {
        session.flushTimer = null;
        emitFrame(browser).catch(() => {});
      }, 100);
    };

    const startFrames = (browser) => {
      stopFrames();
      session.frames = setInterval(() => {
        emitFrame(browser).catch(() => {});
      }, 400);
    };

    const publishResult = async (stopped, { subsystem } = {}) => {
      if (session.published) return;
      session.published = true;
      session.finalizing = true;
      stopFrames();
      const draft = structuredClone(stopped.result);
      const capabilityCount = capabilityCountFromPiResult(draft);
      const summary = await catalog.remember({
        recordingId: session.recordingId,
        action: session.action,
        title: session.title,
        goal: controller.view(session.recordingId).goal,
        result: draft,
        evidenceCount: stopped.session.evidenceCount,
        subsystem,
      });
      logPiOnly(`PI 已提交 ${capabilityCount} 项能力`);
      send(ws, { type: "recording_result_saved", result: summary });
      send(ws, snapshot("editable", {
        label: `PI 已提交 ${capabilityCount} 项能力`,
        progress: { step: "ready", label: `第 1～6 阶段已完成，PI 已产出 ${capabilityCount} 项能力` },
        capture_frozen: true,
        draft,
        draft_fingerprint: session.recordingId,
        assist: { reason: "" },
        human_can_click: false,
      }));
    };

    const failOpen = () => {
      stopFrames();
      send(ws, snapshot("failed", {
        label: publicFailureMessage(),
        error: publicFailureMessage(),
        draft: null,
        assist: { reason: "" },
        human_can_click: false,
      }));
    };

    ws.on("message", async (raw) => {
      let message;
      try {
        message = JSON.parse(String(raw));
      } catch {
        return;
      }
      const type = String(message.type || "");
      try {
        if (type === "ping") {
          send(ws, { type: "pong" });
          return;
        }
        if (type === "attach") {
          const recordingId = String(message.recording_id || message.run_id || "").trim();
          if (!recordingId) {
            send(ws, { type: "error", detail: "缺少 recording_id，无法接回这场录制" });
            return;
          }
          let view;
          try {
            view = controller.reattach(recordingId, {
              onThought: think,
              onComplete: async (payload) => {
                try {
                  await publishResult(payload, { subsystem: message.subsystem });
                } catch (error) {
                  logPiOnly(`自动定稿推送失败：${error?.message || error}`);
                }
              },
              onAssist: () => {
                send(ws, snapshot("recording", {
                  label: "PI 正在自动操作，预览你也可以点",
                  progress: {
                    step: "capturing",
                    label: "已暂停自动操作，请协助。做完后说继续。",
                    request_count: controller.view(recordingId).evidenceCount,
                  },
                  ...viewExtra(),
                }));
              },
              onFailed: () => {
                if (session.published) return;
                failOpen();
                send(ws, { type: "error", detail: publicFailureMessage() });
              },
            });
          } catch (error) {
            send(ws, { type: "error", detail: error.message || "无法接回这场录制" });
            return;
          }
          clearDisconnectCancel(recordingId);
          session.recordingId = recordingId;
          session.action = String(view.action || session.action || "");
          session.title = String(view.title || session.title || "");
          session.closed = false;
          startFrames(controller.browserOf?.(recordingId));
          logPiOnly(`前台已接回录制 recording=${recordingId}`);
          think(
            view.assist_paused || String(view.assist?.reason || "").trim()
              ? "前台已重新接上，仍在等人协助。预览你继续点，做完后说继续。"
              : "前台已重新接上，PI 继续这场录制，预览你也可以点",
          );
          send(ws, snapshot("recording", {
            label: view.publicMessage || "PI 正在自动操作，预览你也可以点",
            progress: {
              step: "capturing",
              label: view.publicMessage || "PI 正在自动操作，预览你也可以点",
              request_count: view.evidenceCount,
            },
            ...viewExtra(),
          }));
          return;
        }
        if (type === "start") {
          let resolveStarted = () => {};
          session.started = new Promise((resolve) => {
            resolveStarted = resolve;
          });
          session.action = String(message.resume_action || newAction());
          session.title = String(message.title || "").trim();
          session.published = false;
          session.finalizing = false;
          logPiOnly("正在启动 PI；旧录制逻辑绝不启动");
          think("PI 是唯一语义决策者；旧录制逻辑绝不启动。正在启动 PI。");
          send(ws, snapshot("recording", {
            label: "正在启动 PI",
            progress: { step: "capturing", label: "正在启动 PI" },
          }));
          let started;
          try {
            started = await controller.start({
              targetUrl: message.start_url,
              goal: message.goal_text || message.title,
              title: session.title,
              action: session.action,
              storageState: message.storage_state || null,
              viewport: message.viewport || null,
              onThought: think,
              onComplete: async (payload) => {
                try {
                  await publishResult(payload, { subsystem: message.subsystem });
                } catch (error) {
                  logPiOnly(`自动定稿推送失败：${error?.message || error}`);
                }
              },
              onAssist: () => {
                send(ws, snapshot("recording", {
                  label: "PI 正在自动操作，预览你也可以点",
                  progress: {
                    step: "capturing",
                    label: "已暂停自动操作，请协助。做完后说继续。",
                    request_count: controller.view(session.recordingId).evidenceCount,
                  },
                  ...viewExtra(),
                }));
              },
              onFailed: () => {
                if (session.published) return;
                failOpen();
                send(ws, { type: "error", detail: publicFailureMessage() });
              },
            });
            session.recordingId = started.id;
            resolveStarted();
            if (session.closed && !session.finalizing && !session.published) {
              await controller.cancel(started.id).catch(() => {});
              return;
            }
          } catch (error) {
            resolveStarted();
            throw error;
          }
          const browser = controller.browserOf?.(started.id);
          if (message.viewport) {
            await browser?.setViewport?.(message.viewport);
          }
          startFrames(browser);
          logPiOnly(`PI 已启动 recording=${started.id}`);
          think("PI 开始自动操作，你也可以点预览。能力边做边交。");
          send(ws, snapshot("recording", {
            label: "PI 正在自动操作，预览你也可以点",
            progress: {
              step: "capturing",
              label: "PI 正在自动操作，预览你也可以点",
              request_count: started.evidenceCount,
            },
            ...viewExtra(),
          }));
          return;
        }
        if (type === "viewport") {
          const browser = controller.browserOf?.(session.recordingId);
          try {
            await browser?.setViewport?.(message);
            await emitFrame(browser);
          } catch {
            // 视口调整失败不得编造能力
          }
          return;
        }
        if (type === "steer" || type === "pi_message") {
          if (!session.recordingId) return;
          const text = String(message.text || message.message || "").trim();
          if (!text) {
            send(ws, { type: "input_error", detail: "请输入要发给 PI 的话" });
            return;
          }
          try {
            const steered = await controller.steer(session.recordingId, text);
            const uiStatus = session.finalizing ? "processing" : "recording";
            send(ws, snapshot(uiStatus, {
              label: steered.publicMessage || "已发给 PI，正在继续",
              progress: {
                step: session.finalizing ? "analyzing" : "capturing",
                label: steered.publicMessage || "已发给 PI，正在继续",
                request_count: steered.evidenceCount,
              },
              capture_frozen: session.finalizing,
              ...viewExtra(),
            }));
          } catch (error) {
            send(ws, { type: "input_error", detail: error.message || "没有发给 PI" });
          }
          return;
        }
        if (type === "abort" || type === "stop_pi") {
          if (!session.recordingId) return;
          try {
            await controller.stopPiWork(session.recordingId);
            send(ws, snapshot(session.finalizing ? "processing" : "recording", {
              label: "已终止当前自动操作",
              progress: {
                step: session.finalizing ? "analyzing" : "capturing",
                label: "已终止当前自动操作，预览你继续点，或再发一句话继续",
                request_count: controller.view(session.recordingId).evidenceCount,
              },
              capture_frozen: session.finalizing,
              ...viewExtra(),
            }));
          } catch (error) {
            send(ws, { type: "input_error", detail: error.message || "无法终止" });
          }
          return;
        }
        if (type === "input") {
          const browser = controller.browserOf?.(session.recordingId);
          session.inputChain = (session.inputChain || Promise.resolve())
            .catch(() => {})
            .then(async () => {
              await browser?.applyInput?.(message.event || {});
              scheduleFlush(browser);
            })
            .catch((error) => {
              send(ws, { type: "input_error", detail: error.message || "页面操作没有执行" });
            });
          return;
        }
        if (type === "finish") {
          await session.started;
          if (session.published) return;
          session.finalizing = true;
          const current = session.recordingId ? controller.view(session.recordingId) : null;
          if (current?.status === "succeeded" && current.hasFinalResult) {
            const payload = await controller.result(session.recordingId);
            await publishResult({
              session: payload.session,
              result: payload.result,
              receipt: payload.receipt,
            }, { subsystem: message.subsystem });
            return;
          }
          logPiOnly("自动操作结束，浏览器保持打开，等待 PI 提交已完成的能力");
          think("自动操作结束，等待 PI 提交已完成的能力。预览画面继续更新。");
          send(ws, snapshot("processing", {
            label: "等待 PI 提交能力",
            progress: { step: "freezing", label: "等待 PI 提交已完成的能力" },
            capture_frozen: true,
            ...viewExtra(),
          }));
          const stopped = await controller.stop(session.recordingId);
          await publishResult(stopped, { subsystem: message.subsystem });
          return;
        }
        if (type === "cancel") {
          session.finalizing = false;
          stopFrames();
          try {
            await controller.cancel(session.recordingId);
          } catch {
            // 取消必然失败且无能力
          }
          send(ws, snapshot("failed", {
            label: publicFailureMessage(),
            error: publicFailureMessage(),
            draft: null,
            assist: { reason: "" },
            human_can_click: false,
          }));
        }
      } catch (error) {
        stopFrames();
        const reason = error?.message || String(error);
        logPiOnly(`[PI分析] 录制失败：${reason}`);
        send(ws, snapshot("failed", {
          label: publicFailureMessage(),
          error: publicFailureMessage(),
          draft: null,
          assist: { reason: "" },
          human_can_click: false,
        }));
        send(ws, { type: "error", detail: publicFailureMessage() });
      }
    });

    ws.on("close", () => {
      session.closed = true;
      stopFrames();
      const recordingId = session.recordingId;
      if (!recordingId) return;
      let view = null;
      try {
        view = controller.view(recordingId);
      } catch {
        return;
      }
      if (!shouldCancelOnFrontendDisconnect(view, { finalizing: session.finalizing })) {
        logPiOnly(
          isAssistHoldView(view)
            ? `前台断开，正在等人协助，不取消这场录制 recording=${recordingId}`
            : `前台断开，证据已冻结或正在最终分析，继续等 PI recording=${recordingId}`,
        );
        return;
      }
      const graceMs = Math.max(20, Number(disconnectGraceMs) || FRONTEND_DISCONNECT_GRACE_MS);
      logPiOnly(`前台断开，${Math.round(graceMs / 1000)}s 内重连则继续这场录制 recording=${recordingId}`);
      scheduleDisconnectCancel(controller, recordingId, graceMs);
    });
  });

  return wss;
}
