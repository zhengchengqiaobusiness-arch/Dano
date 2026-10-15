import { writeFile, mkdir, access } from "node:fs/promises";
import path from "node:path";
import { recordingDir } from "../paths.mjs";
import { originFromUrl } from "../session-store.mjs";
import { browserSession, raceWall, sameDocumentNav, withPage } from "./session.mjs";
import { locatorFor, overlayControlFacts, overlayExactBlock, overlayFactLists, overlayHasRef, overlaySurfaceKind, takeSnapshot, yamlFingerprint } from "./snapshot.mjs";
import { beginAction, endAction, listNetwork, waitForAction } from "./network.mjs";
import { logLine } from "../log.mjs";

const PROBE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

const ACTIONS = new Set(["open", "snapshot", "click", "fill", "fill_fields", "press", "select", "upload", "screenshot"]);
export const ACT_WALL_MS = 15000;

async function fileFieldAttrs(el) {
  if (!el || typeof el.getAttribute !== "function") return {};
  const name = String(await el.getAttribute("name").catch(() => "") || "").trim();
  const accept = String(await el.getAttribute("accept").catch(() => "") || "").trim();
  const field = {};
  if (name) field.name = name;
  if (accept) field.accept = accept;
  return field.name || field.accept ? { file_field: field } : {};
}

export function fieldFillText(field) {
  if (!field || typeof field !== "object") return null;
  if (typeof field.text === "string") return field.text;
  if (typeof field.value === "string") return field.value;
  return null;
}

export function withFillFieldText(input) {
  if (!input || input.action !== "fill_fields" || !Array.isArray(input.fields)) return input;
  return {
    ...input,
    fields: input.fields.map((field) => {
      const text = fieldFillText(field);
      return field && typeof field === "object" && text != null ? { ...field, text } : field;
    }),
  };
}

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
  const snapshot = await takeSnapshot(recordingId, { budgetMs: 2500 });
  return withOverlayFacts(recordingId, { ok: false, error: "stale_ref", snapshot });
}

async function needsClick(locator) {
  const readonly = await locator.getAttribute("readonly").catch(() => null);
  const aria = await locator.getAttribute("aria-readonly").catch(() => null);
  if (readonly != null || aria === "true") return true;
  const kind = await locator.evaluate((node) => {
    const el = node instanceof Element ? node : null;
    if (!el) return "";
    const role = String(el.getAttribute("role") || "").toLowerCase();
    const nested = el.matches("input") ? el : el.querySelector("input");
    const type = nested instanceof HTMLInputElement ? String(nested.type || "") : "";
    return role === "slider" || type === "range" ? "slider" : "";
  }).catch(() => "");
  return kind === "slider";
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
  timer = setTimeout(() => finish(null), 1500);
  return {
    promise,
    stop() {
      finish(null);
    },
  };
}

function pointerBlocked(error) {
  return /intercepts pointer|obscures|subtree intercepts/i.test(String(error?.message || ""));
}

function clickUnreachable(error) {
  return pointerBlocked(error) || /not visible|outside of the viewport|Timeout/i.test(String(error?.message || ""));
}

export function clickFailureKind(error) {
  const message = String(error?.message || error || "");
  if (pointerBlocked(error)) return "behind_overlay";
  if (/not visible/i.test(message)) return "not_visible";
  if (/outside of the viewport/i.test(message)) return "outside_viewport";
  if (/Timeout/i.test(message)) return "click_timeout";
  return "click_failed";
}

function samePageUrl(current, next) {
  try {
    return new URL(current).href === new URL(next).href;
  } catch {
    return String(current || "") === String(next || "");
  }
}

async function snapshotAfter(recordingId, budgetMs = 3000, { retryEmpty = false } = {}) {
  const snapshot = await takeSnapshot(recordingId, { budgetMs });
  if (!retryEmpty || (snapshot?.refs || []).length) return snapshot;
  await new Promise((resolve) => setTimeout(resolve, 800));
  return takeSnapshot(recordingId, { budgetMs });
}

async function withOverlayFacts(recordingId, result) {
  if (!result) return result;
  try {
    const overlay_fields = await overlayControlFacts(recordingId);
    const snapshotText = result.snapshot?.text || browserSession(recordingId)?.lastPublished?.text || "";
    return { ...result, ...overlayFactLists(overlay_fields), overlay_surface: overlaySurfaceKind(snapshotText) };
  } catch {
    return result;
  }
}

export function runAction(recordingId, input) {
  return raceWall(
    withPage(recordingId, () => perform(recordingId, input)),
    ACT_WALL_MS,
    () => ({
      ok: false,
      error: "act_timeout",
      snapshot: browserSession(recordingId)?.lastPublished || { epoch: 0, text: "", refs: [] },
      hint: "这一下还没结束。下一次页面工具会等它结束；ref 只用之后返回的新树。",
    }),
  );
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
      const snapshot = await snapshotAfter(recordingId);
      return { ok: false, error: "origin_denied", snapshot };
    }
    if (samePageUrl(state.page.url(), url)) {
      return withOverlayFacts(recordingId, {
        ok: true,
        snapshot: await snapshotAfter(recordingId, 3000, { retryEmpty: true }),
      });
    }
    let emptyRetry = true;
    let snapMs = 3000;
    const beforeOverlay = yamlFingerprint(overlayExactBlock(state.lastPublished?.text || ""));
    const sameDoc = sameDocumentNav(state.page.url(), url);
    try {
      await state.page.goto(url, {
        waitUntil: sameDoc ? "commit" : "domcontentloaded",
        timeout: sameDoc ? 4000 : 8000,
      });
    } catch {
      if (originFromUrl(state.page.url()) !== state.origin) {
        return { ok: false, error: "origin_denied", snapshot: await snapshotAfter(recordingId, 2000) };
      }
      emptyRetry = false;
      snapMs = 2500;
    }
    let snapshot = await snapshotAfter(recordingId, snapMs, { retryEmpty: emptyRetry });
    if (sameDoc) {
      for (let i = 0; i < 3; i += 1) {
        const nowOverlay = yamlFingerprint(overlayExactBlock(snapshot.text || ""));
        if (beforeOverlay && nowOverlay !== beforeOverlay) break;
        if (!beforeOverlay && i > 0) break;
        await new Promise((resolve) => setTimeout(resolve, 400));
        snapshot = await snapshotAfter(recordingId, snapMs, { retryEmpty: emptyRetry });
      }
    }
    return withOverlayFacts(recordingId, { ok: true, snapshot });
  }
  if (action === "snapshot") {
    return withOverlayFacts(recordingId, { ok: true, snapshot: await snapshotAfter(recordingId, 8000) });
  }
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
      epoch: state.epoch,
      snap: state.snap || 0,
      hint: "这张图不换树。点、填只用最近一次 snapshot 树里的 [ref=]。",
    };
  }
  if (action !== "fill_fields" && !String(input.ref || "").trim()) {
    return { ok: false, error: "ref_required", hint: `${action} 要带最近一次快照树里的 [ref=fN:eN@数字]。`, snapshot: state.lastPublished || await snapshotAfter(recordingId) };
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
      const fields = withFillFieldText(input).fields || [];
      const filled = [];
      for (const field of fields) {
        const text = fieldFillText(field);
        if (!field || typeof field.ref !== "string" || text == null) {
          return withOverlayFacts(recordingId, { ok: false, error: "value_not_applied", ref: field?.ref || "", filled, snapshot: await snapshotAfter(recordingId) });
        }
        const item = locatorFor(recordingId, field.ref);
        if (!item) return { ...(await stale(recordingId)), ref: field.ref, filled };
        if (await needsClick(item.locator)) {
          return withOverlayFacts(recordingId, {
            ok: false,
            error: "needs_click",
            ref: field.ref,
            filled,
            snapshot: state.lastPublished || await snapshotAfter(recordingId),
          });
        }
        try {
          await item.locator.fill(text, { timeout: 2000 });
        } catch {
          return withOverlayFacts(recordingId, {
            ok: false,
            error: "value_not_applied",
            ref: field.ref,
            filled,
            snapshot: state.lastPublished || await snapshotAfter(recordingId),
          });
        }
        if (!(await readBack(item.locator, text))) {
          return withOverlayFacts(recordingId, { ok: false, error: "value_not_applied", ref: field.ref, filled, snapshot: await snapshotAfter(recordingId) });
        }
        if (item.label) filled.push(item.label);
      }
      return withOverlayFacts(recordingId, {
        ok: true,
        filled,
        snapshot: state.lastPublished || await snapshotAfter(recordingId),
      });
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
        hint: "文件选择还开着。对打开它的那个 ref 调用 upload。",
      };
    }
    if (action === "click") {
      state.agentClick = true;
      await hit.locator.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => {});
      try {
        await hit.locator.click({ trial: true, timeout: 2000 });
      } catch (error) {
        if (pointerBlocked(error)) {
          return withOverlayFacts(recordingId, {
            ok: false,
            error: "behind_overlay",
            clicked: hit.label || "",
            snapshot: await snapshotAfter(recordingId),
          });
        }
      }
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
          const kind = clickFailureKind(error);
          if (kind === "behind_overlay" || kind === "not_visible" || kind === "outside_viewport" || kind === "click_timeout") {
            return withOverlayFacts(recordingId, {
              ok: false,
              error: kind,
              clicked: hit.label || "",
              snapshot: await snapshotAfter(recordingId),
              hint: kind === "behind_overlay"
                ? "这个 ref 被当前层挡住。只用返回的 overlay_exact 与新树。"
                : "这个 ref 当前点不到。重新 snapshot，只点新树里的 [ref=]。不要改点同名的另一个。",
            });
          }
          const message = String(error?.message || "click_failed").split("\n")[0];
          logLine(`[browser] click failed ${message}`);
          return { ok: false, error: message, requests: requestsDuring(), snapshot: await snapshotAfter(recordingId) };
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
            hint: `文件没有写入：${String(error?.message || error).split("\n")[0]}。对同一 ref 调用 upload。`,
          };
        }
        const fileField = await fileFieldAttrs(typeof chooser.element === "function" ? chooser.element() : hit.locator);
        await waitForAction(recordingId, actionId);
        const requests = requestsDuring();
        logLine(`[browser] upload accepted ref=${input.ref || ""} requests=${requests.map((row) => row.path).join(",") || "-"}`);
        return {
          ok: true,
          uploaded: true,
          clicked: hit.label || "",
          requests,
          snapshot: await snapshotAfter(recordingId),
          ...fileField,
        };
      }
    }
    if (action === "fill") {
      if (await needsClick(hit.locator)) {
        return withOverlayFacts(recordingId, {
          ok: false,
          error: "needs_click",
          ref: input.ref,
          snapshot: state.lastPublished || await snapshotAfter(recordingId),
        });
      }
      try {
        await hit.locator.fill(String(input.text ?? ""), { timeout: 2000 });
      } catch {
        return withOverlayFacts(recordingId, { ok: false, error: "value_not_applied", snapshot: await snapshotAfter(recordingId) });
      }
      if (!(await readBack(hit.locator, String(input.text ?? "")))) {
        return withOverlayFacts(recordingId, { ok: false, error: "value_not_applied", snapshot: await snapshotAfter(recordingId) });
      }
    }
    if (action === "press") await hit.locator.press(String(input.key || input.text || ""));
    if (action === "select") await hit.locator.selectOption(String(input.text ?? ""));
    let filled = [];
    let fileField = {};
    if (action === "fill" || action === "select") filled = [hit.label || ""].filter(Boolean);
    if (action === "upload") {
      const dir = path.join(recordingDir(recordingId), "uploads");
      await mkdir(dir, { recursive: true });
      const filePath = String(input.file_path || "").trim() || path.join(dir, "attachment.png");
      if (!input.file_path) await writeFile(filePath, PROBE_PNG);
      else {
        try {
          await access(filePath);
        } catch {
          return withOverlayFacts(recordingId, {
            ok: false,
            error: "file_not_found",
            ref: input.ref,
            hint: "这个路径不存在。不要为了上传去造文件；不带 file_path 调用 upload 会用自带的探测图片。",
            snapshot: state.lastPublished || await snapshotAfter(recordingId),
          });
        }
      }
      try {
        if (state.pendingFileChooser) {
          const chooser = state.pendingFileChooser;
          await chooser.setFiles(filePath);
          fileField = await fileFieldAttrs(typeof chooser.element === "function" ? chooser.element() : hit.locator);
          state.pendingFileChooser = null;
        } else {
          await hit.locator.setInputFiles(filePath);
          fileField = await fileFieldAttrs(hit.locator);
        }
      } catch (error) {
        const message = String(error?.message || error);
        if (/not an HTMLInputElement|setInputFiles/i.test(message)) {
          return withOverlayFacts(recordingId, {
            ok: false,
            error: "needs_upload",
            ref: input.ref,
            hint: "这一格不是文件框。先 click 打开选择器，再对返回 needs_upload 的那个 ref 调用 upload。不要编一个不存在的路径。",
            snapshot: state.lastPublished || await snapshotAfter(recordingId),
          });
        }
        return { ok: false, error: message.split("\n")[0], snapshot: await snapshotAfter(recordingId) };
      }
      filled = [hit.label || ""].filter(Boolean);
    }
    if (action === "click" || action === "upload" || action === "fill" || action === "select") {
      await waitForAction(recordingId, actionId);
    }
    const requests = requestsDuring();
    if (action === "fill" || action === "select") {
      return withOverlayFacts(recordingId, {
        ok: true,
        filled,
        requests,
        snapshot: state.lastPublished || await snapshotAfter(recordingId),
      });
    }
    const beforeSnap = state.lastPublished;
    const prevKey = state.lastYamlKey || "";
    const snapshot = await snapshotAfter(recordingId);
    const key = yamlFingerprint(snapshot.text);
    state.lastYamlKey = key;
    const unchanged = action === "click" && key === prevKey && !requests.length;
    state.pageStall = unchanged ? (state.pageStall || 0) + 1 : 0;
    const inOverlay = action === "click" && overlayHasRef(beforeSnap?.text, input.ref);
    const layerIdle = inOverlay && !requests.length;
    return action === "click"
      ? withOverlayFacts(recordingId, {
          ok: true,
          clicked: hit.label || "",
          requests,
          snapshot,
          ...(inOverlay ? { in_overlay: true } : {}),
          ...(layerIdle ? { layer_idle: true } : {}),
          ...(state.pageStall >= 2 ? { page_unchanged: true } : {}),
        })
      : { ok: true, filled, requests, snapshot, ...fileField };
  } finally {
    state.agentClick = false;
    endAction(recordingId);
  }
}
