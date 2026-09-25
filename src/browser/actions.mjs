import { writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { recordingDir } from "../paths.mjs";
import { originFromUrl } from "../session-store.mjs";
import { browserSession } from "./session.mjs";
import { locatorFor, takeSnapshot } from "./snapshot.mjs";
import { beginAction, endAction, listNetwork, waitForAction } from "./network.mjs";

const PROBE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

const ACTIONS = new Set(["open", "snapshot", "click", "fill", "fill_fields", "press", "select", "upload", "screenshot"]);

async function readBack(locator, text) {
  const aria = await locator.getAttribute("aria-valuenow");
  if (aria != null) return aria === String(text);
  const editable = await locator.getAttribute("contenteditable");
  if (editable === "" || editable === "true") {
    const inner = await locator.innerText().catch(() => "");
    return inner.includes(String(text));
  }
  try {
    return (await locator.inputValue()) === String(text);
  } catch {
    const inner = await locator.innerText().catch(() => "");
    return inner.includes(String(text));
  }
}

async function stale(recordingId) {
  const snapshot = await takeSnapshot(recordingId);
  return { ok: false, error: "stale_ref", snapshot };
}

export async function runAction(recordingId, input) {
  const action = String(input?.action || "");
  if (!ACTIONS.has(action)) return { ok: false, error: "unknown_action" };
  const state = browserSession(recordingId);
  if (!state) return { ok: false, error: "no_browser" };
  if (action === "open") {
    const url = String(input.url || "");
    if (originFromUrl(url) !== state.origin) {
      const snapshot = await takeSnapshot(recordingId);
      return { ok: false, error: "origin_denied", snapshot };
    }
    await state.page.goto(url, { waitUntil: "domcontentloaded" });
    return { ok: true, snapshot: await takeSnapshot(recordingId) };
  }
  if (action === "snapshot") return { ok: true, snapshot: await takeSnapshot(recordingId) };
  if (action === "screenshot") {
    const target = input.ref ? locatorFor(recordingId, input.ref)?.locator : state.page;
    if (input.ref && !target) return stale(recordingId);
    const bytes = input.ref
      ? await target.screenshot({ type: "png" })
      : await state.page.screenshot({ type: "png" });
    return {
      ok: true,
      __image: true,
      as_image: true,
      mimeType: "image/png",
      data: Buffer.from(bytes).toString("base64"),
    };
  }
  const hit = locatorFor(recordingId, input.ref);
  const frame = hit?.frame || null;
  const actionId = beginAction(recordingId, frame);
  const requestsDuring = () => listNetwork(recordingId, { action_id: actionId }).map((row) => ({
    id: row.id,
    method: row.method,
    path: row.path,
  }));
  try {
    if (action === "fill_fields") {
      const fields = Array.isArray(input.fields) ? input.fields : [];
      const filled = [];
      for (const field of fields) {
        if (!field || typeof field.ref !== "string" || typeof field.text !== "string") {
          return { ok: false, error: "value_not_applied", ref: field?.ref || "", snapshot: await takeSnapshot(recordingId) };
        }
        const item = locatorFor(recordingId, field.ref);
        if (!item) return { ...(await stale(recordingId)), ref: field.ref };
        await item.locator.fill(field.text);
        if (!(await readBack(item.locator, field.text))) {
          return { ok: false, error: "value_not_applied", ref: field.ref, snapshot: await takeSnapshot(recordingId) };
        }
        if (item.label) filled.push(item.label);
      }
      return { ok: true, filled, snapshot: await takeSnapshot(recordingId) };
    }
    if (!hit) {
      console.log(`[browser] ${action} ref=${input.ref || ""} stale`);
      return stale(recordingId);
    }
    console.log(`[browser] ${action} ref=${input.ref || ""}`);
    if (action === "click") {
      const chooserWait = state.page.waitForEvent("filechooser", { timeout: 8000 }).catch(() => null);
      try {
        await hit.locator.click({ timeout: 8000 });
      } catch (error) {
        const chooser = state.pendingFileChooser || await Promise.race([
          chooserWait,
          new Promise((resolve) => setTimeout(() => resolve(null), 400)),
        ]);
        if (chooser) {
          state.pendingFileChooser = chooser;
          return { ok: false, error: "needs_upload", ref: input.ref, requests: requestsDuring(), snapshot: await takeSnapshot(recordingId) };
        }
        const message = String(error?.message || "click_failed").split("\n")[0];
        console.log(`[browser] click failed ${message}`);
        return { ok: false, error: message, requests: requestsDuring(), snapshot: await takeSnapshot(recordingId) };
      }
      const chooser = state.pendingFileChooser || await Promise.race([
        chooserWait,
        new Promise((resolve) => setTimeout(() => resolve(null), 800)),
      ]);
      if (chooser) {
        state.pendingFileChooser = chooser;
        return { ok: false, error: "needs_upload", ref: input.ref, requests: requestsDuring(), snapshot: await takeSnapshot(recordingId) };
      }
    }
    if (action === "fill") {
      try {
        await hit.locator.fill(String(input.text ?? ""));
      } catch {
        return { ok: false, error: "value_not_applied", snapshot: await takeSnapshot(recordingId) };
      }
      if (!(await readBack(hit.locator, String(input.text ?? "")))) {
        return { ok: false, error: "value_not_applied", snapshot: await takeSnapshot(recordingId) };
      }
    }
    if (action === "press") await hit.locator.press(String(input.key || input.text || ""));
    if (action === "select") await hit.locator.selectOption(String(input.text ?? ""));
    let filled = [];
    if (action === "fill" || action === "select") filled = [hit.label || ""].filter(Boolean);
    if (action === "upload") {
      const dir = path.join(recordingDir(recordingId), "uploads");
      await mkdir(dir, { recursive: true });
      const filePath = String(input.file_path || "").trim() || path.join(dir, "attachment.png");
      if (!input.file_path) await writeFile(filePath, PROBE_PNG);
      if (state.pendingFileChooser) {
        await state.pendingFileChooser.setFiles(filePath);
        state.pendingFileChooser = null;
      } else {
        await hit.locator.setInputFiles(filePath);
      }
      filled = [hit.label || ""].filter(Boolean);
    }
    if (action === "click" || action === "upload" || action === "fill" || action === "select") {
      await waitForAction(recordingId, actionId);
    }
    const snapshot = await takeSnapshot(recordingId);
    const requests = requestsDuring();
    return action === "click"
      ? { ok: true, clicked: hit.label || "", requests, snapshot }
      : { ok: true, filled, requests, snapshot };
  } finally {
    endAction(recordingId);
  }
}
