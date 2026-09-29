import { writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { recordingDir } from "../paths.mjs";
import { originFromUrl } from "../session-store.mjs";
import { browserSession, withPage } from "./session.mjs";
import { choiceClick, currentControlLabel, focusAfterChoice, labelsMatch, locatorFor, sameColumnRefs, snapshotHasControl, takeSnapshot } from "./snapshot.mjs";
import { armAction, beginAction, endAction, listNetwork, waitForAction, withRequestChanges } from "./network.mjs";
import { logLine } from "../log.mjs";

const PROBE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

const ACTIONS = new Set(["open", "snapshot", "click", "fill", "fill_fields", "press", "select", "upload", "screenshot"]);

async function readValue(locator) {
  const ariaNow = await locator.getAttribute("aria-valuenow").catch(() => null);
  const ariaText = await locator.getAttribute("aria-valuetext").catch(() => null);
  if (ariaNow) return ariaNow;
  if (ariaText) return ariaText;
  let input = "";
  try {
    input = await locator.inputValue();
  } catch {
    input = "";
  }
  if (input) return input;
  return ((await locator.innerText().catch(() => "")) || "").replace(/\s+/g, " ").trim();
}

export function acceptFill(before, after, typed) {
  const norm = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const prev = norm(before);
  const next = norm(after);
  const want = norm(typed);
  if (next === want) return { ok: true, value: next };
  if (next && next !== prev) return { ok: true, value: next };
  return { ok: false, value: next };
}

async function afterWrite(locator, typed, write) {
  const type = await locator.getAttribute("type").catch(() => null);
  const secret = type === "password";
  const before = secret ? "" : await readValue(locator);
  try {
    await write();
  } catch {
    return { ok: false, secret, value: "" };
  }
  if (secret) return { ok: await readBack(locator, typed).catch(() => false), secret: true, value: "" };
  const after = await readValue(locator);
  let decision = acceptFill(before, after, typed);
  if (!decision.ok && await readBack(locator, typed).catch(() => false)) {
    decision = { ok: true, value: decision.value || String(typed ?? "").replace(/\s+/g, " ").trim() };
  }
  return { ok: decision.ok, secret: false, value: decision.value };
}

async function readBack(locator, text) {
  const expected = String(text);
  const aria = await locator.getAttribute("aria-valuenow");
  if (aria != null) return aria === expected;
  const picked = locator.locator("option:checked");
  if (await picked.count().catch(() => 0)) {
    const label = ((await picked.first().textContent().catch(() => "")) || "").trim();
    const value = await locator.inputValue().catch(() => "");
    return label === expected || value === expected;
  }
  const editable = await locator.getAttribute("contenteditable");
  if (editable === "" || editable === "true") {
    const inner = await locator.innerText().catch(() => "");
    return inner.includes(expected);
  }
  try {
    return (await locator.inputValue()) === expected;
  } catch {
    const inner = await locator.innerText().catch(() => "");
    return inner.includes(expected);
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function takeReadySnapshot(recordingId) {
  let snapshot = await takeSnapshot(recordingId);
  let waited = false;
  const deadline = Date.now() + 6000;
  while (!snapshotHasControl(snapshot.text) && Date.now() < deadline) {
    waited = true;
    await delay(400);
    snapshot = await takeSnapshot(recordingId);
  }
  return { snapshot, waited };
}

function notApplied(ref, snapshot) {
  return {
    ok: false,
    error: "value_not_applied",
    ...(ref ? { ref } : {}),
    hint: "这个控件没有留下这次填写的字。只用返回的 snapshot。若里面新出现了格子或选项，点那些 ref。",
    snapshot,
  };
}

async function stale(recordingId, ref = "") {
  const snapshot = await takeSnapshot(recordingId);
  return {
    ok: false,
    error: "stale_ref",
    ...(ref ? { ref } : {}),
    hint: "这个 ref 上的名字已经和快照不同，或快照已过期。只用新快照里的 ref。",
    snapshot,
  };
}

async function nameStill(hit, recordingId) {
  if (!hit?.label) return true;
  const live = await currentControlLabel(hit.locator, hit.label);
  const extras = (browserSession(recordingId)?.controls || [])
    .filter((node) => node.role === "button" || node.role === "link")
    .map((node) => node.name);
  return labelsMatch(hit.label, live, extras);
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

function notedLabel(hit) {
  if (!hit?.label) return "";
  const place = [hit.popup ? `popup="${hit.popup}"` : "", hit.row ? `row="${hit.row}"` : ""].filter(Boolean).join(" ");
  return place ? `${hit.label} ${place}` : hit.label;
}

function notedValue(hit, value) {
  return { label: hit.label, value, ...(hit.popup ? { popup: hit.popup } : {}), ...(hit.row ? { row: hit.row } : {}) };
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
    const actionId = beginAction(recordingId, state.page.mainFrame(), { anyFrame: true });
    try {
      armAction(recordingId);
      await state.page.goto(url, { waitUntil: "domcontentloaded" });
      const ready = await takeReadySnapshot(recordingId);
      if (ready.waited) await delay(400);
      await waitForAction(recordingId, actionId);
      const requests = withRequestChanges(recordingId, listNetwork(recordingId, { action_id: actionId }));
      const snapshot = requests.length ? await takeSnapshot(recordingId) : ready.snapshot;
      return { ok: true, action_id: actionId, requests, snapshot, ...(snapshot.changed ? { changed: snapshot.changed } : {}) };
    } finally {
      endAction(recordingId);
    }
  }
  if (action === "snapshot") return { ok: true, snapshot: (await takeReadySnapshot(recordingId)).snapshot };
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
  const frames = [];
  for (const field of Array.isArray(input.fields) ? input.fields : []) {
    const item = locatorFor(recordingId, field?.ref);
    if (item?.frame && !frames.includes(item.frame)) frames.push(item.frame);
  }
  const frame = hit?.frame || frames[0] || null;
  const actionId = beginAction(recordingId, frame, frames.length ? { frames } : {});
  const tag = (payload) => ({ ...payload, action_id: actionId });
  const requestsDuring = () => withRequestChanges(recordingId, listNetwork(recordingId, { action_id: actionId }));
  try {
    if (action === "fill_fields") {
      const fields = Array.isArray(input.fields) ? input.fields : [];
      const filled = [];
      const filledValue = [];
      armAction(recordingId);
      for (const field of fields) {
        if (!field || typeof field.ref !== "string" || typeof field.text !== "string") {
          return tag(notApplied(field?.ref || "", await takeSnapshot(recordingId)));
        }
        const item = locatorFor(recordingId, field.ref);
        if (!item) return tag(await stale(recordingId, field.ref));
        if (!(await nameStill(item, recordingId))) return tag(await stale(recordingId, field.ref));
        const wrote = await afterWrite(item.locator, field.text, () => item.locator.fill(field.text, { timeout: 2000 }));
        if (!wrote.ok) {
          await waitForAction(recordingId, actionId);
          const snapshot = await takeSnapshot(recordingId);
          return tag({
            ...notApplied(field.ref, snapshot),
            requests: requestsDuring(),
            ...(snapshot.changed ? { changed: snapshot.changed } : {}),
          });
        }
        if (item.label) {
          filled.push(notedLabel(item));
          if (!wrote.secret && wrote.value) filledValue.push(notedValue(item, wrote.value));
        }
      }
      await waitForAction(recordingId, actionId);
      const snapshot = await takeSnapshot(recordingId);
      return tag({
        ok: true,
        filled,
        ...(filledValue.length ? { filled_value: filledValue } : {}),
        requests: requestsDuring(),
        snapshot,
        ...(snapshot.changed ? { changed: snapshot.changed } : {}),
      });
    }
    if (!hit) {
      logLine(`[browser] ${action} ref=${input.ref || ""} stale`);
      return tag(await stale(recordingId, input.ref));
    }
    if (!(await nameStill(hit, recordingId))) {
      logLine(`[browser] ${action} ref=${input.ref || ""} name changed`);
      return tag(await stale(recordingId, input.ref));
    }
    logLine(`[browser] ${action} ref=${input.ref || ""}`);
    if (action === "click" && state.pendingFileChooser) {
      return tag({
        ok: false,
        error: "needs_upload",
        ref: input.ref,
        hint: "文件选择还开着。对打开它的那个 ref 调用 upload。不要点关闭、取消，也不要改去提交。",
      });
    }
    if (action === "click") {
      state.agentClick = true;
      await hit.locator.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => {});
      const chooserArm = armChooser(state.page);
      let chooser = null;
      try {
        armAction(recordingId);
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
          await waitForAction(recordingId, actionId);
          const snapshot = await takeSnapshot(recordingId);
          return tag({
            ok: false,
            error: "click_timeout",
            ref: input.ref,
            hint: "这一下没有点中。只用返回的 snapshot 里的 ref，不要再点刚才这个 ref。",
            requests: requestsDuring(),
            snapshot,
          });
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
          armAction(recordingId);
          await chooser.setFiles(filePath);
        } catch (error) {
          state.pendingFileChooser = chooser;
          return tag({
            ok: false,
            error: "needs_upload",
            ref: input.ref,
            hint: `文件没有写入：${String(error?.message || error).split("\n")[0]}。对同一 ref 调用 upload，不要点关闭。`,
          });
        }
        await waitForAction(recordingId, actionId);
        const requests = requestsDuring();
        logLine(`[browser] upload accepted ref=${input.ref || ""} requests=${requests.map((row) => row.path).join(",") || "-"}`);
        return tag({
          ok: true,
          uploaded: true,
          clicked: hit.label || "",
          ...(hit.popup ? { popup: hit.popup } : {}),
          ...(hit.row ? { row: hit.row } : {}),
          requests,
          snapshot: await takeSnapshot(recordingId),
          hint: "文件已写入这次选择。采用 requests 里的上传请求写进附件。不要再点关闭。",
        });
      }
    }
    let wrote = null;
    if (action === "fill") {
      armAction(recordingId);
      wrote = await afterWrite(hit.locator, String(input.text ?? ""), () => hit.locator.fill(String(input.text ?? ""), { timeout: 2000 }));
      if (!wrote.ok) return tag(notApplied(input.ref, await takeSnapshot(recordingId)));
    }
    let pressedValue = "";
    if (action === "press") {
      const type = await hit.locator.getAttribute("type").catch(() => null);
      const before = type === "password" ? "" : await readValue(hit.locator);
      armAction(recordingId);
      await hit.locator.press(String(input.key || input.text || ""));
      if (type !== "password") {
        const after = String(await readValue(hit.locator) || "").replace(/\s+/g, " ").trim();
        const prev = String(before || "").replace(/\s+/g, " ").trim();
        if (after && after !== prev) pressedValue = after;
      }
    }
    if (action === "select") {
      const text = String(input.text ?? "");
      armAction(recordingId);
      wrote = await afterWrite(hit.locator, text, () => hit.locator.selectOption(text, { timeout: 2000 }));
      if (!wrote.ok) return tag(notApplied(input.ref, await takeSnapshot(recordingId)));
    }
    let filled = [];
    let filledValue = [];
    if (action === "fill" || action === "select") {
      filled = [notedLabel(hit)].filter(Boolean);
      if (hit.label && wrote && !wrote.secret && wrote.value) filledValue = [notedValue(hit, wrote.value)];
    }
    if (action === "press" && pressedValue && hit.label) {
      filled = [notedLabel(hit)];
      filledValue = [notedValue(hit, pressedValue)];
    }
    if (action === "upload") {
      const dir = path.join(recordingDir(recordingId), "uploads");
      await mkdir(dir, { recursive: true });
      const filePath = String(input.file_path || "").trim() || path.join(dir, "attachment.png");
      if (!input.file_path) await writeFile(filePath, PROBE_PNG);
      armAction(recordingId);
      if (state.pendingFileChooser) {
        await state.pendingFileChooser.setFiles(filePath);
        state.pendingFileChooser = null;
      } else {
        await hit.locator.setInputFiles(filePath);
      }
      filled = [notedLabel(hit)].filter(Boolean);
    }
    if (action === "click" || action === "upload" || action === "fill" || action === "select" || action === "press") {
      await waitForAction(recordingId, actionId);
    }
    const choice = action === "click" && choiceClick(hit?.label || "");
    const held = [];
    if (choice) {
      for (const node of browserSession(recordingId)?.controls || []) {
        if (!node?.states?.includes("active")) continue;
        if (!["textbox", "searchbox", "combobox"].includes(node.role)) continue;
        const current = locatorFor(recordingId, node.ref);
        if (!current?.locator) continue;
        held.push({
          role: node.role,
          name: node.name,
          placeholder: node.placeholder || "",
          popup: node.popup || "",
          row: node.row || "",
          label: node.shown || node.role,
          before: node.value || "",
          value: await readValue(current.locator),
        });
      }
    }
    const previous = [...(browserSession(recordingId)?.controls || [])];
    const ready = await takeReadySnapshot(recordingId);
    if (ready.waited) await waitForAction(recordingId, actionId);
    const snapshot = ready.waited ? await takeSnapshot(recordingId) : ready.snapshot;
    const requests = requestsDuring();
    const after = browserSession(recordingId)?.controls || [];
    const focus = focusAfterChoice(held, previous, after);
    const sameColumn = requests.length ? [] : sameColumnRefs(snapshot.text, hit.label);
    const changed = snapshot.changed ? { changed: snapshot.changed } : {};
    return tag(action === "click"
      ? {
          ok: true,
          clicked: hit.label || "",
          ...(hit.popup ? { popup: hit.popup } : {}),
          ...(hit.row ? { row: hit.row } : {}),
          requests,
          snapshot,
          ...changed,
          ...(focus.focused_unchanged ? { focused_unchanged: focus.focused_unchanged } : {}),
          ...(focus.focused_value ? { focused_value: focus.focused_value } : {}),
          ...(sameColumn.length ? { same_column: sameColumn } : {}),
        }
      : { ok: true, filled, ...(filledValue.length ? { filled_value: filledValue } : {}), requests, snapshot, ...changed });
  } finally {
    state.agentClick = false;
    endAction(recordingId);
  }
}
