import { originFromUrl } from "../session-store.mjs";
import { browserSession } from "./session.mjs";
import { locatorFor, takeSnapshot } from "./snapshot.mjs";
import { beginAction, endAction, listNetwork, waitForAction } from "./network.mjs";

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
      }
      return { ok: true, snapshot: await takeSnapshot(recordingId) };
    }
    if (!hit) {
      console.log(`[browser] ${action} ref=${input.ref || ""} stale`);
      return stale(recordingId);
    }
    console.log(`[browser] ${action} ref=${input.ref || ""}`);
    if (action === "click") {
      try {
        await hit.locator.click({ timeout: 8000 });
      } catch (error) {
        const message = String(error?.message || "click_failed").split("\n")[0];
        console.log(`[browser] click failed ${message}`);
        return { ok: false, error: message, requests: requestsDuring(), snapshot: await takeSnapshot(recordingId) };
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
    if (action === "upload") await hit.locator.setInputFiles(String(input.file_path || ""));
    if (action === "click") await waitForAction(recordingId, actionId);
    const snapshot = await takeSnapshot(recordingId);
    const requests = requestsDuring();
    return action === "click"
      ? { ok: true, clicked: hit.label || "", requests, snapshot }
      : { ok: true, requests, snapshot };
  } finally {
    endAction(recordingId);
  }
}
