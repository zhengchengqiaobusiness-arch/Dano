import { writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { recordingDir } from "../paths.mjs";
import { originFromUrl } from "../session-store.mjs";
import { browserSession, withPage } from "./session.mjs";
import { locatorFor, sameColumnRefs, takeSnapshot, yamlFingerprint } from "./snapshot.mjs";
import { beginAction, endAction, listNetwork, waitForAction } from "./network.mjs";
import { logLine } from "../log.mjs";

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

function looksLikeFilter(hit) {
  return Promise.all([
    hit.locator.getAttribute("role"),
    hit.locator.getAttribute("aria-haspopup"),
    hit.locator.getAttribute("aria-autocomplete"),
  ]).then(([role, popup, auto]) => /\b(combobox|listbox|list)\b/i.test(`${role || ""} ${popup || ""} ${auto || ""}`)).catch(() => false);
}

function armChooser(page) {
  let done = false;
  let timer;
  const onChooser = (chooser) => finish(chooser);
  function finish(chooser) {
    if (done) return;
    done = true;
    clearTimeout(timer);
    page.off("filechooser", onChooser);
    resolveChooser(chooser);
  }
  let resolveChooser = () => {};
  const promise = new Promise((resolve) => {
    resolveChooser = resolve;
  });
  page.on("filechooser", onChooser);
  timer = setTimeout(() => finish(null), 8000);
  return {
    promise,
    stop() {
      finish(null);
    },
  };
}

export function runAction(recordingId, input) {
  return withPage(recordingId, () => perform(recordingId, input));
}

async function perform(recordingId, input) {
  let action = String(input?.action || "");
  if (action === "type") action = "fill";
  if (action === "key") action = "press";
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
  const actionId = beginAction(recordingId, frame, { anyFrame: true });
  const requestsDuring = () => listNetwork(recordingId, { action_id: actionId }).map((row) => ({
    id: row.id,
    method: row.method,
    path: row.path,
    keys: row.keys,
  }));
  try {
    if (action === "fill_fields") {
      const fields = Array.isArray(input.fields) ? input.fields : [];
      const filled = [];
      let waitFilter = false;
      for (const field of fields) {
        if (!field || typeof field.ref !== "string" || typeof field.text !== "string") {
          return { ok: false, error: "value_not_applied", ref: field?.ref || "", snapshot: await takeSnapshot(recordingId) };
        }
        const item = locatorFor(recordingId, field.ref);
        if (!item) return { ...(await stale(recordingId)), ref: field.ref };
        await item.locator.fill(field.text, { timeout: 2000 });
        if (!(await readBack(item.locator, field.text))) {
          return { ok: false, error: "value_not_applied", ref: field.ref, snapshot: await takeSnapshot(recordingId) };
        }
        if (item.label) filled.push(item.label);
        if (await looksLikeFilter(item)) waitFilter = true;
      }
      await waitForAction(recordingId, actionId);
      return {
        ok: true,
        filled,
        requests: requestsDuring(),
        snapshot: await takeSnapshot(recordingId, waitFilter ? { minObserve: 2500 } : {}),
      };
    }
    if (!hit) {
      logLine(`[browser] ${action} ref=${input.ref || ""} stale`);
      return stale(recordingId);
    }
    logLine(`[browser] ${action} ref=${input.ref || ""}`);
    if (action === "click" && state.pendingFileChooser) {
      return {
        ok: false,
        error: "needs_upload",
        ref: input.ref,
        hint: "文件选择还开着。对打开它的那个 ref 调用 upload。不要点关闭、取消，也不要改去提交。",
      };
    }
    const before = (action === "click" || action === "press") ? await yamlFingerprint(recordingId) : "";
    if (action === "click") {
      state.agentClick = true;
      await hit.locator.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => {});
      const chooserArm = armChooser(state.page);
      let chooser = null;
      try {
        await hit.locator.click({ timeout: 8000 });
      } catch (error) {
        chooser = state.pendingFileChooser || await Promise.race([
          chooserArm.promise,
          new Promise((resolve) => setTimeout(() => resolve(null), 200)),
        ]);
        if (!chooser) {
          chooserArm.stop();
          const message = String(error?.message || "click_failed").split("\n")[0];
          logLine(`[browser] click failed ${message}`);
          return {
            ok: false,
            error: "click_timeout",
            hint: "这一下没有点中。只用返回的 snapshot 里的 ref。",
            requests: requestsDuring(),
            snapshot: await takeSnapshot(recordingId, { changedFrom: before }),
          };
        }
      }
      if (!chooser) {
        chooser = state.pendingFileChooser || await Promise.race([
          chooserArm.promise,
          new Promise((resolve) => setTimeout(() => resolve(null), 300)),
        ]);
      }
      chooserArm.stop();
      if (chooser) {
        state.pendingFileChooser = null;
        const dir = path.join(recordingDir(recordingId), "uploads");
        await mkdir(dir, { recursive: true });
        const filePath = path.join(dir, "attachment.png");
        await writeFile(filePath, PROBE_PNG);
        try {
          await chooser.setFiles(filePath);
        } catch (error) {
          state.pendingFileChooser = chooser;
          return {
            ok: false,
            error: "needs_upload",
            ref: input.ref,
            hint: `文件没有写入：${String(error?.message || error).split("\n")[0]}。对同一 ref 调用 upload，不要点关闭。`,
          };
        }
        await waitForAction(recordingId, actionId);
        const requests = requestsDuring();
        logLine(`[browser] upload accepted ref=${input.ref || ""} requests=${requests.map((row) => row.path).join(",") || "-"}`);
        return {
          ok: true,
          uploaded: true,
          clicked: hit.label || "",
          requests,
          snapshot: await takeSnapshot(recordingId, { changedFrom: before }),
          hint: "文件已写入这次选择。采用 requests 里的上传请求写进附件。不要再点关闭。",
        };
      }
    }
    if (action === "fill") {
      try {
        await hit.locator.fill(String(input.text ?? ""), { timeout: 2000 });
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
    if (action === "click" || action === "upload" || action === "fill" || action === "select" || action === "press") {
      await waitForAction(recordingId, actionId);
    }
    const snapOpts = before ? { changedFrom: before } : {};
    if ((action === "fill" || action === "select") && await looksLikeFilter(hit)) snapOpts.minObserve = 2500;
    const snapshot = await takeSnapshot(recordingId, snapOpts);
    const requests = requestsDuring();
    const sameColumn = sameColumnRefs(snapshot.text, hit.label);
    return action === "click"
      ? {
          ok: true,
          clicked: hit.label || "",
          requests,
          snapshot,
          ...(sameColumn.length ? { same_column: sameColumn } : {}),
        }
      : { ok: true, filled, requests, snapshot };
  } finally {
    state.agentClick = false;
    endAction(recordingId);
  }
}

