import { WebSocketServer } from "ws";
import { appendEvidence } from "./evidence/store.mjs";
import { browserSession, captureFrame, closeBrowser, getPage, openBrowser } from "./browser/session.mjs";
import { beginAction, endAction, listNetwork, waitForAction } from "./browser/network.mjs";
import { loadStorageState, saveStorageState } from "./session-store.mjs";
import { createRecording, emit, persist, snapshotMessage } from "./session.mjs";
import { hostTools } from "./agent/tools.mjs";
import { startRecordingPi as defaultStart } from "./agent/pi-session.mjs";

export function attachWebSocket(server, { startRecordingPi = defaultStart } = {}) {
  const wss = new WebSocketServer({ noServer: true });
  server.on("upgrade", (req, socket, head) => {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    if (url.pathname !== "/onboarding/page/record") {
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req));
  });
  wss.on("connection", (ws) => {
    let recording = null;
    let frameTimer = null;
    let seq = 0;
    const send = (message) => {
      if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(message));
    };
    const pushFrame = async () => {
      if (!recording) return;
      const frame = await captureFrame(recording.id);
      if (!frame?.data) return;
      seq += 1;
      send({ type: "frame", seq, data: frame.data, width: frame.width, height: frame.height });
    };
    ws.on("message", async (raw) => {
      let message;
      try {
        message = JSON.parse(String(raw));
      } catch {
        send({ type: "error", detail: "invalid_json" });
        return;
      }
      if (message.type === "ping") {
        send({ type: "pong" });
        return;
      }
      if (message.type === "start") {
        const stored = message.storage_state || await loadStorageState(message.start_url);
        recording = await createRecording(message);
        recording.emitThought = (thought) => send({ type: "thought", ...thought });
        recording.emit = () => {
          send(snapshotMessage(recording));
          persist(recording);
        };
        try {
          await openBrowser({
            recordingId: recording.id,
            url: recording.startUrl,
            storageState: stored,
            viewport: recording.viewport,
          });
        } catch (error) {
          send({ type: "error", detail: error.message });
          return;
        }
        await pushFrame();
        frameTimer = setInterval(pushFrame, 400);
        send(snapshotMessage(recording));
        const tools = hostTools(recording);
        try {
          recording.pi = await startRecordingPi({ recordingId: recording.id, tools, recording });
          recording.emit();
        } catch (error) {
          send({ type: "error", detail: error.message });
        }
        return;
      }
      if (!recording) {
        send({ type: "error", detail: "not_started" });
        return;
      }
      if (message.type === "file") {
        const state = browserSession(recording.id);
        const chooser = state?.pendingFileChooser;
        if (!chooser) {
          send({ type: "input_error", detail: "没有打开的文件选择" });
          return;
        }
        const name = String(message.name || "attachment.bin");
        const bytes = Buffer.from(String(message.data || ""), "base64");
        const actionId = beginAction(recording.id, null, { anyFrame: true });
        try {
          await chooser.setFiles({ name, mimeType: "application/octet-stream", buffer: bytes });
          state.pendingFileChooser = null;
          await waitForAction(recording.id, actionId);
          const requests = listNetwork(recording.id, { action_id: actionId });
          await appendEvidence(recording.id, {
            kind: "action",
            summary: `upload:${name}`,
            body: { ok: true, filled: [name], requests },
            body_missing: false,
          });
        } catch (error) {
          send({ type: "input_error", detail: error.message });
        } finally {
          endAction(recording.id);
        }
        setTimeout(pushFrame, 100);
        return;
      }
      if (message.type === "viewport") {
        const page = getPage(recording.id);
        const width = Math.round(Number(message.width) || 0);
        const height = Math.round(Number(message.height) || 0);
        if (page && width >= 320 && height >= 240) {
          await page.setViewportSize({ width, height }).catch(() => {});
        }
        setTimeout(pushFrame, 50);
        return;
      }
      if (message.type === "input") {
        const page = getPage(recording.id);
        const event = message.event || {};
        const viewport = page?.viewportSize() || { width: 1440, height: 900 };
        const x = Number.isFinite(Number(event.x)) ? Number(event.x) : Math.round(Number(event.nx || 0) * viewport.width);
        const y = Number.isFinite(Number(event.y)) ? Number(event.y) : Math.round(Number(event.ny || 0) * viewport.height);
        const kind = String(event.kind || "");
        try {
          if (kind === "pointer_move") {
            const now = Date.now();
            if (now - (recording.lastPointerMoveAt || 0) < 50) return;
            recording.lastPointerMoveAt = now;
            await page.mouse.move(x, y);
          } else if (kind === "pointer_down") {
            recording.manualAction = beginAction(recording.id, null, { anyFrame: true });
            await page.mouse.move(x, y);
            await page.mouse.down({ button: event.button || "left" });
          } else if (kind === "pointer_up") {
            await page.mouse.move(x, y);
            await page.mouse.up({ button: event.button || "left" });
            const actionId = recording.manualAction || "";
            recording.manualAction = "";
            if (actionId) {
              await waitForAction(recording.id, actionId);
              endAction(recording.id);
              const requests = listNetwork(recording.id, { action_id: actionId });
              await appendEvidence(recording.id, {
                kind: "action",
                summary: "manual_click",
                body: { ok: true, requests },
                body_missing: false,
              });
            }
            if (browserSession(recording.id)?.pendingFileChooser) send({ type: "needs_file" });
          } else if (kind === "scroll") {
            await page.mouse.wheel(Number(event.dx || 0), Number(event.dy || 0));
          } else if (kind === "text" && event.text) {
            await page.keyboard.type(String(event.text));
          } else if (kind === "key" && event.key) {
            await page.keyboard.press(String(event.key));
          } else {
            send({ type: "input_error", detail: kind || "unknown" });
            return;
          }
        } catch (error) {
          send({ type: "input_error", detail: error.message });
          return;
        }
        setTimeout(pushFrame, 100);
        return;
      }
      if (message.type === "steer" || message.type === "pi_message") {
        const text = message.text || "人已继续，从当前页面接着做";
        if (typeof recording.releaseAssist === "function") {
          const release = recording.releaseAssist;
          recording.releaseAssist = null;
          release(text);
          return;
        }
        recording.paused = false;
        if (recording.status === "waiting_operator") recording.status = "recording";
        recording.assistReason = "";
        send(snapshotMessage(recording));
        try {
          await recording.pi?.prompt(text);
        } catch (error) {
          send({ type: "error", detail: error.message });
        }
        recording.emit();
        return;
      }
      if (message.type === "abort" || message.type === "stop_pi" || message.type === "cancel") {
        recording.finished = true;
        if (typeof recording.releaseAssist === "function") {
          const release = recording.releaseAssist;
          recording.releaseAssist = null;
          release("stopped");
        }
        await recording.pi?.dispose?.();
        const page = getPage(recording.id);
        if (page) await saveStorageState(recording.startUrl, await page.context().storageState());
        recording.status = "stopped";
        clearInterval(frameTimer);
        await closeBrowser(recording.id);
        recording.emit();
      }
    });
    ws.on("close", () => {
      clearInterval(frameTimer);
      if (!recording) return;
      recording.finished = true;
      if (typeof recording.releaseAssist === "function") {
        const release = recording.releaseAssist;
        recording.releaseAssist = null;
        release("stopped");
      }
      closeBrowser(recording.id);
    });
  });
}
