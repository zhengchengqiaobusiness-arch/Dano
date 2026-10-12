import { WebSocketServer } from "ws";
import { appendEvidence } from "./evidence/store.mjs";
import { browserSession, captureFrame, closeBrowser, getPage, openBrowser, withoutPageLock, withPage } from "./browser/session.mjs";
import { beginAction, compactNetworkIndex, endAction, listNetwork, waitForAction } from "./browser/network.mjs";
import { overlayControlFacts, overlayFactLists, takeSnapshot } from "./browser/snapshot.mjs";
import { loadStorageState, saveStorageState } from "./session-store.mjs";
import { appendSessionThought, createRecording, emit, getRecording, loadRecording, persist, persistThoughts, snapshotMessage } from "./session.mjs";
import { hostTools } from "./agent/tools.mjs";
import { startRecordingPi as defaultStart } from "./agent/pi-session.mjs";
import { armIdleTimer, HUMAN_STEER_IDLE_MS, PREVIEW_DETACH_MS } from "./idle-timer.mjs";

function recordingIsLive(recording) {
  return Boolean(recording && !recording.finished && recording.status !== "stopped" && recording.status !== "failed");
}

function attachPreview(recording, ws, send) {
  clearTimeout(recording.detachTimer);
  recording.detachTimer = null;
  recording.previewSocket = ws;
  recording.emitThought = (thought) => {
    appendSessionThought(recording, thought);
    send({ type: "thought", ...thought });
  };
  recording.emit = () => {
    send(snapshotMessage(recording));
    persist(recording);
  };
  persistThoughts(recording).catch(() => {});
  send({ type: "thoughts", items: recording.thoughts || [] });
}

function scheduleDetach(recording) {
  recording.emitThought = (thought) => appendSessionThought(recording, thought);
  persistThoughts(recording).catch(() => {});
  armIdleTimer(recording, "detachTimer", PREVIEW_DETACH_MS, () => {
    if (recording.previewSocket) return;
    recording.finished = true;
    recording.status = "stopped";
    persistThoughts(recording).catch(() => {});
    recording.pi?.dispose?.();
    closeBrowser(recording.id);
  });
}

function scheduleHumanSteer(recording) {
  armIdleTimer(recording, "humanSteerTimer", HUMAN_STEER_IDLE_MS, () => {
    steerAfterHumanAct(recording);
  });
}

async function steerAfterHumanAct(recording) {
  if (!recording?.pi || !recordingIsLive(recording) || recording.paused) return;
  if (!browserSession(recording.id)) return;
  const listed = compactNetworkIndex(listNetwork(recording.id));
  let overlay_fields = [];
  try {
    await withPage(recording.id, () => takeSnapshot(recording.id, { budgetMs: 2000 }));
    overlay_fields = await overlayControlFacts(recording.id);
  } catch {
    overlay_fields = [];
  }
  const facts = overlayFactLists(overlay_fields);
  const text = `人在预览里操作了。actor=human。先 browser_snapshot 再接着做。\n${JSON.stringify({ listed, ...facts })}`;
  recording.pi.prompt(text).catch(() => {});
}

async function applyPointer(recording, event, send) {
  const page = getPage(recording.id);
  const viewport = page?.viewportSize() || { width: 1440, height: 900 };
  const x = Number.isFinite(Number(event.x)) ? Number(event.x) : Math.round(Number(event.nx || 0) * viewport.width);
  const y = Number.isFinite(Number(event.y)) ? Number(event.y) : Math.round(Number(event.ny || 0) * viewport.height);
  const kind = String(event.kind || "");
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
        body: { ok: true, actor: "human", requests },
        body_missing: false,
      });
      recording.emitThought?.({
        kind: "tool",
        phase: "end",
        ok: true,
        text: `manual_click 完成 requests=${requests.length}`,
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
  }
}

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
      if (message.type === "attach" || message.type === "start") {
        const existingId = String(message.recording_id || message.run_id || "");
        const existing = existingId ? (getRecording(existingId) || await loadRecording(existingId)) : null;
        if (existing) {
          recording = existing;
          attachPreview(recording, ws, send);
          if (getPage(recording.id)) {
            await pushFrame();
            frameTimer = setInterval(pushFrame, 400);
          }
          send(snapshotMessage(recording));
          return;
        }
        if (message.type === "attach") {
          send({ type: "error", detail: "not_started" });
          return;
        }
        const stored = message.storage_state || await loadStorageState(message.start_url);
        recording = await createRecording(message);
        attachPreview(recording, ws, send);
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
          await withPage(recording.id, () => chooser.setFiles({ name, mimeType: "application/octet-stream", buffer: bytes }));
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
        setTimeout(pushFrame, 50);
        return;
      }
      if (message.type === "input") {
        try {
          await withoutPageLock(recording.id, () => applyPointer(recording, message.event || {}, send));
        } catch (error) {
          send({ type: "input_error", detail: error.message });
          return;
        }
        const kind = String(message.event?.kind || "");
        if (kind === "pointer_up" || kind === "text" || kind === "key") {
          scheduleHumanSteer(recording);
        }
        setTimeout(pushFrame, 100);
        return;
      }
      if (message.type === "steer" || message.type === "pi_message") {
        const text = message.text || "人已继续，从当前页面接着做";
        if (!recording.pi) {
          recording.earlySteer = recording.earlySteer || [];
          recording.earlySteer.push(text);
          return;
        }
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
        clearTimeout(recording.detachTimer);
        recording.previewSocket = null;
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
        persistThoughts(recording).catch(() => {});
        clearInterval(frameTimer);
        await closeBrowser(recording.id);
        recording.emit();
      }
    });
    ws.on("close", () => {
      clearInterval(frameTimer);
      if (!recording) return;
      if (recording.previewSocket === ws) recording.previewSocket = null;
      persistThoughts(recording).catch(() => {});
      if (recordingIsLive(recording)) {
        scheduleDetach(recording);
        return;
      }
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
