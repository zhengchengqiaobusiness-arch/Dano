import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { recordingDir } from "../paths.mjs";
import { appendEvidence } from "../evidence/store.mjs";
import { changedRequestKeys, keysAddedSince, previousMatchingRequest, previousPathRequest, requestIndexRow, sameAsUnlinked, writerRequestRows } from "../skillpack/request-keys.mjs";
import { noteLoginBody, noteRequestHeaders } from "../auth-vault.mjs";

const NOISE = /sockjs|websocket|favicon\.ico|\/[^/?#]+\.(?:json|js|css|map|svg|png|woff2?|ttf|ico)(?:[?#]|$)/i;
const bags = new Map();

function bag(recordingId) {
  if (!bags.has(recordingId)) bags.set(recordingId, { items: [], seq: 0, action: null, pages: [] });
  return bags.get(recordingId);
}

export function beginAction(recordingId, frame, { anyFrame = false, frames = null } = {}) {
  const state = bag(recordingId);
  state.seq += 1;
  const action = {
    id: `act_${state.seq}`,
    frame,
    frames: frames?.length ? frames : (frame ? [frame] : []),
    anyFrame,
    started: Date.now(),
    open: true,
  };
  state.action = action;
  return action.id;
}

export const ACTION_WINDOW_MS = 1500;

export function armAction(recordingId) {
  const action = bag(recordingId).action;
  if (!action?.open || Number.isFinite(action.armed)) return;
  action.armed = Date.now();
}

export function sealedUntil(action, now, windowMs = ACTION_WINDOW_MS) {
  const origin = Number.isFinite(action?.armed) ? action.armed : action?.started;
  return (Number.isFinite(origin) ? origin : now) + windowMs;
}

export function sealAction(recordingId) {
  const action = bag(recordingId).action;
  if (!action?.open) return;
  action.acceptUntil = sealedUntil(action, Date.now());
}

export function actionAccepts(action, started, windowMs = ACTION_WINDOW_MS) {
  if (!action?.open) return false;
  const at = Number(started);
  if (!Number.isFinite(at)) return false;
  const origin = Number.isFinite(action.armed) ? action.armed : action.started;
  if (!Number.isFinite(origin) || at < origin) return false;
  if (Number.isFinite(action.acceptUntil)) return at <= action.acceptUntil;
  if (Number.isFinite(action.armed)) return true;
  return at - origin <= windowMs;
}

function frameChain(frame) {
  const chain = [];
  let current = frame;
  const seen = new Set();
  while (current && !seen.has(current)) {
    seen.add(current);
    chain.push(current);
    try {
      current = current.parentFrame?.() || null;
    } catch {
      break;
    }
  }
  return chain;
}

export function framesRelated(clicked, request) {
  if (!clicked || !request) return false;
  if (sameFrame(clicked, request)) return true;
  return frameChain(clicked).slice(1).some((frame) => sameFrame(frame, request));
}

export function pageOpenedFor(action, requestFrame, pages) {
  if (!action?.open || !requestFrame) return false;
  let page = null;
  try {
    page = requestFrame.page?.() || null;
  } catch {
    return false;
  }
  if (!page) return false;
  const opened = (pages || []).find((item) => item.page === page);
  if (!opened) return false;
  const origin = Number.isFinite(action.armed) ? action.armed : action.started;
  if (!Number.isFinite(origin) || opened.at < origin) return false;
  let opener = null;
  try {
    opener = page.opener?.() || null;
  } catch {
    return false;
  }
  if (!opener) return false;
  return (action.frames || []).some((frame) => {
    try {
      return frame.page?.() === opener;
    } catch {
      return false;
    }
  });
}

function inAction(action, frame, pages) {
  if (!action?.open) return false;
  if (action.anyFrame) return true;
  if ((action.frames || []).some((item) => framesRelated(item, frame))) return true;
  return pageOpenedFor(action, frame, pages);
}

export function labelAction(recordingId, actionId, summary) {
  if (!actionId || !summary) return;
  for (const item of bag(recordingId).items) {
    if (item.action_id === actionId) item.action = summary;
  }
}

export function endAction(recordingId) {
  const state = bag(recordingId);
  if (state.action) state.action.open = false;
}

export function attachNetwork(recordingId, context) {
  const state = bag(recordingId);
  context.on("page", (page) => {
    state.pages.push({ page, at: Date.now() });
  });
  context.on("request", (request) => {
    const type = request.resourceType();
    const script = type === "script";
    if (!script && type !== "xhr" && type !== "fetch") return;
    if (!script && NOISE.test(request.url())) return;
    if (!script) noteRequestHeaders(recordingId, request.headers());
    state.seq += 1;
    const id = script ? `js_${state.seq}` : `req_${state.items.length + 1}`;
    const started = Date.now();
    const item = {
      id,
      method: request.method(),
      url: request.url(),
      path: new URL(request.url()).pathname,
      query: new URL(request.url()).search.replace(/^\?/, ""),
      post_data: request.postData() || "",
      status: 0,
      response_body: null,
      body_missing: true,
      started_at: started,
      ended_at: 0,
      action_id: "",
      frame: request.frame(),
      content_type: "",
      kind: script ? "script" : "request",
    };
    const action = state.action;
    if (!script && actionAccepts(action, started) && inAction(action, item.frame, state.pages)) {
      item.action_id = action.id;
    }
    state.items.push(item);
    request._cabpId = id;
  });
  context.on("response", async (response) => {
    const request = response.request();
    const item = state.items.find((row) => row.id === request._cabpId);
    if (!item) return;
    item.status = response.status();
    item.ended_at = Date.now();
    item.content_type = response.headers()["content-type"] || "";
    if (item.kind === "script") {
      try {
        item.response_body = await response.text();
        item.body_missing = false;
      } catch {
        item.response_body = null;
        item.body_missing = true;
      }
      return;
    }
    const action = state.action;
    if (!item.action_id && actionAccepts(action, item.started_at) && inAction(action, item.frame, state.pages)) {
      item.action_id = action.id;
    }
    try {
      item.response_body = await response.text();
      item.body_missing = false;
      noteLoginBody(recordingId, item.response_body, { method: item.method, url: item.url });
    } catch {
      item.response_body = null;
      item.body_missing = true;
    }
    const stored = {
      id: item.id,
      method: item.method,
      url: item.url,
      path: item.path,
      query: item.query,
      post_data: item.post_data,
      status: item.status,
      response_body: item.response_body,
      body_missing: item.body_missing,
      started_at: item.started_at,
      ended_at: item.ended_at,
      action_id: item.action_id,
    };
    const file = path.join(recordingDir(recordingId), "blobs", `${item.id}.json`);
    await mkdir(path.dirname(file), { recursive: true });
    await writeFile(file, JSON.stringify(stored));
    await appendEvidence(recordingId, {
      id: item.id,
      kind: "network",
      summary: `${item.method} ${item.path}`,
      body_missing: item.body_missing,
      body: stored,
    }).catch(() => {});
  });
}

function sameFrame(left, right) {
  if (!left || !right) return false;
  if (left === right) return true;
  const key = (frame) => {
    const guid = frame._guid || "";
    if (guid) return guid;
    const parent = frame.parentFrame?.();
    const parentId = parent?._guid || parent?.url?.() || "";
    const name = frame.name?.() || "";
    return `${parentId}\n${name}\n${frame.url?.() || ""}`;
  };
  try {
    return key(left) === key(right);
  } catch {
    return false;
  }
}

export function actionSettled({ now, started, timeout = ACTION_WINDOW_MS, quiet = 400, rows = [] }) {
  if (now - started >= timeout) return true;
  const pending = rows.some((row) => !row.ended_at);
  const last = rows.reduce((at, row) => Math.max(at, row.ended_at || row.started_at || 0), started);
  return !pending && now - last >= quiet;
}

export function waitForAction(recordingId, actionId, timeout = ACTION_WINDOW_MS, quiet = 400) {
  const state = bag(recordingId);
  const started = Date.now();
  return new Promise((resolve) => {
    const tick = () => {
      const rows = state.items.filter((row) => row.action_id === actionId);
      if (actionSettled({ now: Date.now(), started, timeout, quiet, rows })) resolve();
      else setTimeout(tick, 50);
    };
    tick();
  });
}

export function scriptExcerpts(source, find, { limit = 5, radius = 180 } = {}) {
  const text = String(source || "");
  const needle = String(find || "");
  if (needle.length < 2) return [];
  const out = [];
  let from = 0;
  while (out.length < limit) {
    const at = text.indexOf(needle, from);
    if (at < 0) break;
    const start = Math.max(0, at - radius);
    const end = Math.min(text.length, at + needle.length + radius);
    out.push(text.slice(start, end));
    from = at + needle.length;
  }
  return out;
}

export function listScripts(recordingId) {
  const seen = new Set();
  const out = [];
  for (const row of bag(recordingId).items) {
    if (row.kind !== "script" || row.body_missing || !row.path || seen.has(row.path)) continue;
    seen.add(row.path);
    out.push({ id: row.id, path: row.path });
  }
  return out;
}

export function listNetwork(recordingId, { after_id = "", action_id = "" } = {}) {
  let rows = bag(recordingId).items.filter((row) => row.kind !== "script");
  if (after_id) {
    const at = rows.findIndex((row) => row.id === after_id);
    rows = at >= 0 ? rows.slice(at + 1) : rows;
  }
  if (action_id) rows = rows.filter((row) => row.action_id === action_id);
  return rows.map((row) => {
    const index = requestIndexRow(row);
    return {
      id: row.id,
      method: row.method,
      path: row.path,
      status: row.status,
      body_bytes: row.response_body == null ? 0 : Buffer.byteLength(String(row.response_body)),
      body_missing: row.body_missing,
      action_id: row.action_id,
      keys: index.keys,
      ...(index.query ? { query: index.query } : {}),
      ...(index.empty ? { empty: index.empty } : {}),
      ...(index.issues_credential ? { issues_credential: true } : {}),
    };
  });
}

export function withRequestChanges(recordingId, requests) {
  const items = bag(recordingId).items;
  return (requests || []).map((row) => {
    const at = items.findIndex((item) => item.id === row.id);
    const current = at >= 0 ? items[at] : null;
    const prev = at >= 0 ? previousMatchingRequest(items, at) : null;
    const changed = current && prev ? changedRequestKeys(prev, current) : [];
    const next = changed.length ? { ...row, changed_keys: changed } : { ...row };
    const added = current && at >= 0 ? keysAddedSince(previousPathRequest(items, at), current) : [];
    if (added.length) next.added_keys = added;
    if (!changed.length && sameAsUnlinked(current || row, items)) next.same_as_unlinked = true;
    return next;
  });
}

export function requestKeyIndex(recordingId) {
  const items = bag(recordingId).items.filter((row) => row.kind !== "script");
  const rows = items.map((row, at) => {
    const base = requestIndexRow(row);
    const prev = previousMatchingRequest(items, at);
    const changed = prev ? changedRequestKeys(prev, row) : [];
    const next = changed.length ? { ...base, changed_keys: changed } : base;
    const added = keysAddedSince(previousPathRequest(items, at), row);
    if (added.length) next.added_keys = added;
    if (!changed.length && sameAsUnlinked(row, items)) next.same_as_unlinked = true;
    return next;
  });
  return writerRequestRows(rows);
}

export function getNetwork(recordingId, id) {
  const item = bag(recordingId).items.find((row) => row.id === id);
  if (!item) return null;
  return {
    id: item.id,
    method: item.method,
    url: item.url,
    path: item.path,
    query: item.query,
    post_data: item.post_data,
    status: item.status,
    response_body: item.response_body,
    body_missing: item.body_missing,
    started_at: item.started_at,
    ended_at: item.ended_at,
    action_id: item.action_id,
    content_type: item.content_type,
  };
}
