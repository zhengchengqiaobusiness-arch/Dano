import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { recordingDir } from "../paths.mjs";
import { appendEvidence } from "../evidence/store.mjs";
import { requestIndexRow } from "../skillpack/request-keys.mjs";

const NOISE = /sockjs|websocket|favicon\.ico/i;
const bags = new Map();

function bag(recordingId) {
  if (!bags.has(recordingId)) bags.set(recordingId, { items: [], seq: 0, action: null });
  return bags.get(recordingId);
}

export function beginAction(recordingId, frame) {
  const state = bag(recordingId);
  state.seq += 1;
  const action = { id: `act_${state.seq}`, frame, started: Date.now(), open: true };
  state.action = action;
  return action.id;
}

export function endAction(recordingId) {
  const state = bag(recordingId);
  if (state.action) state.action.open = false;
}

export function attachNetwork(recordingId, context) {
  const state = bag(recordingId);
  context.on("request", (request) => {
    const type = request.resourceType();
    if (type !== "xhr" && type !== "fetch") return;
    if (NOISE.test(request.url())) return;
    state.seq += 1;
    const id = `req_${state.items.length + 1}`;
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
    };
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
    const action = state.action;
    const within = item.ended_at - item.started_at <= 1500;
    const during = action?.open && item.started_at >= action.started;
    if (during && within && action.frame && item.frame === action.frame) item.action_id = action.id;
    try {
      item.response_body = await response.text();
      item.body_missing = false;
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

export function listNetwork(recordingId, { after_id = "", action_id = "" } = {}) {
  let rows = bag(recordingId).items;
  if (after_id) {
    const at = rows.findIndex((row) => row.id === after_id);
    rows = at >= 0 ? rows.slice(at + 1) : rows;
  }
  if (action_id) rows = rows.filter((row) => row.action_id === action_id);
  return rows.map((row) => ({
    id: row.id,
    method: row.method,
    path: row.path,
    status: row.status,
    body_bytes: row.response_body == null ? 0 : Buffer.byteLength(String(row.response_body)),
    body_missing: row.body_missing,
    action_id: row.action_id,
    keys: requestIndexRow(row).keys,
  }));
}

export function requestKeyIndex(recordingId) {
  return bag(recordingId).items.map((row) => requestIndexRow(row));
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
