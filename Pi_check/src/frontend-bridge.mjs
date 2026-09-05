/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 对接现有 PageRecorder：预览尺寸由页面送来，采集按该视口截帧，避免拉伸。
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

export function attachFrontendBridge(httpServer, { controller, catalog }) {
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
      frameSeq: 0,
      frames: null,
      frameBusy: false,
      frameWanted: false,
      flushTimer: null,
      inputChain: Promise.resolve(),
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
        if (type === "start") {
          session.action = String(message.resume_action || newAction());
          session.title = String(message.title || "").trim();
          logPiOnly("正在启动 PI；旧录制逻辑绝不启动");
          think("PI 是唯一语义决策者；旧录制逻辑绝不启动。正在启动 PI。");
          send(ws, snapshot("recording", {
            label: "正在启动 PI",
            progress: { step: "capturing", label: "正在启动 PI" },
          }));
          const started = await controller.start({
            targetUrl: message.start_url,
            goal: message.goal_text || message.title,
            title: session.title,
            action: session.action,
            storageState: message.storage_state || null,
            viewport: message.viewport || null,
            onThought: think,
          });
          session.recordingId = started.id;
          const browser = controller.browserOf?.(started.id);
          if (message.viewport) {
            await browser?.setViewport?.(message.viewport);
          }
          startFrames(browser);
          logPiOnly(`PI 已启动 recording=${started.id}`);
          think("PI 已启动，开始原样采集。能力由 PI 在停止后提交。");
          send(ws, snapshot("recording", {
            label: "正在录制",
            progress: {
              step: "capturing",
              label: "正在录制",
              request_count: started.evidenceCount,
            },
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
        if (type === "input") {
          const browser = controller.browserOf?.(session.recordingId);
          session.inputChain = (session.inputChain || Promise.resolve())
            .catch(() => {})
            .then(async () => {
              await browser?.applyInput?.(message.event || {});
              if (shouldFlushFrame(message.event || {})) scheduleFlush(browser);
            })
            .catch((error) => {
              send(ws, { type: "input_error", detail: error.message || "页面操作没有执行" });
            });
          return;
        }
        if (type === "finish") {
          stopFrames();
          logPiOnly("证据已冻结，等待 PI 调用 submit_recording_result");
          think("证据冻结，等待 PI 提交完整能力。");
          send(ws, snapshot("processing", {
            label: "等待 PI 提交能力",
            progress: { step: "freezing", label: "等待 PI 提交完整能力" },
            capture_frozen: true,
          }));
          const stopped = await controller.stop(session.recordingId);
          const draft = structuredClone(stopped.result);
          const capabilityCount = capabilityCountFromPiResult(draft);
          const summary = await catalog.remember({
            recordingId: session.recordingId,
            action: session.action,
            title: session.title,
            goal: controller.view(session.recordingId).goal,
            result: draft,
            evidenceCount: stopped.session.evidenceCount,
            subsystem: message.subsystem,
          });
          logPiOnly(`PI 已提交 ${capabilityCount} 项能力`);
          send(ws, { type: "recording_result_saved", result: summary });
          send(ws, snapshot("editable", {
            label: `PI 已提交 ${capabilityCount} 项能力`,
            progress: { step: "ready", label: `第 1～6 阶段已完成，PI 已产出 ${capabilityCount} 项能力` },
            capture_frozen: true,
            draft,
            draft_fingerprint: session.recordingId,
          }));
          return;
        }
        if (type === "cancel") {
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
        }));
        send(ws, { type: "error", detail: publicFailureMessage() });
      }
    });

    ws.on("close", () => {
      stopFrames();
      const recordingId = session.recordingId;
      if (!recordingId) return;
      let view = null;
      try {
        view = controller.view(recordingId);
      } catch {
        return;
      }
      if (!view || view.status === "succeeded" || view.hasFinalResult) return;
      controller.cancel(recordingId).catch(() => {});
    });
  });

  return wss;
}
