import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { recordingDir } from "../paths.mjs";
import { appendEvidence } from "../evidence/store.mjs";
import { requestIndexRow, responseKeyPreview } from "../skillpack/request-keys.mjs";
import { hasAccessTokenHeader, mergePackedHeaders, noteLoginBody, noteRequestHeaders, pickCredentialHeaders, usableAuthHeaders } from "../auth-vault.mjs";

const BODY_OMIT_BYTES = 16384;
const KEY_PARSE_BYTES = 32768;

const NOISE = /sockjs|websocket|favicon\.ico/i;
const STATIC_TYPE = /^(?:image|font|audio|video)\/|^text\/css\b|javascript|^application\/(?:wasm|font-|x-font)/i;
const TEXT_TYPE = /json|(?:[/+]xml\b)|^text\/|javascript|x-www-form-urlencoded|graphql|^$/i;
const bags = new Map();

export function foldNetwork(items) {
  const kept = [];
  const folded = { head: 0, static: 0, repeated: [] };
  const idle = new Map();
  for (const row of items) {
    const method = String(row.method || "").toUpperCase();
    if (method === "HEAD" || method === "OPTIONS") {
      folded.head += 1;
      continue;
    }
    if (method === "GET" && !row.query && STATIC_TYPE.test(row.content_type || "")) {
      folded.static += 1;
      continue;
    }
    if (method === "GET" && !row.action_id) {
      const key = `${row.path}?${row.query || ""}`;
      idle.set(key, [...(idle.get(key) || []), row]);
    }
    kept.push(row);
  }
  const drop = new Set();
  for (const rows of idle.values()) {
    if (rows.length < 3) continue;
    for (const row of rows.slice(0, -1)) drop.add(row);
    rows.at(-1)._repeat = rows.length;
    folded.repeated.push({ method: "GET", path: rows[0].path, count: rows.length });
  }
  return { kept: kept.filter((row) => !drop.has(row)), folded };
}

export function foldedSummary(recordingId) {
  return foldNetwork(bag(recordingId).items).folded;
}

function bag(recordingId) {
  if (!bags.has(recordingId)) bags.set(recordingId, { items: [], seq: 0, action: null, actions: [] });
  return bags.get(recordingId);
}

export function beginAction(recordingId, frame, { anyFrame = false } = {}) {
  const state = bag(recordingId);
  state.seq += 1;
  const action = { id: `act_${state.seq}`, frame, anyFrame, started: Date.now(), open: true };
  state.action = action;
  state.actions.push({ id: action.id, started: action.started });
  if (state.actions.length > 50) state.actions.shift();
  return action.id;
}

export function endAction(recordingId) {
  const state = bag(recordingId);
  if (state.action) state.action.open = false;
}

function redactMultipartFiles(raw, contentType) {
  const text = Buffer.isBuffer(raw) ? raw.toString("latin1") : String(raw || "");
  if (!/name="/.test(text)) return text;
  const boundary = (String(contentType || "").match(/boundary=([^;]+)/i) || [])[1];
  if (!boundary) return text;
  const token = `--${String(boundary).replace(/^["']|["']$/g, "")}`;
  return text.split(token).map((part) => {
    if (!/filename=/i.test(part)) return part;
    const breakAt = part.search(/\r\n\r\n|\n\n/);
    if (breakAt < 0) return part;
    const header = part.slice(0, breakAt);
    const rest = part.slice(breakAt).replace(/^(?:\r\n\r\n|\n\n)/, "");
    const body = rest.replace(/\r\n$/, "").replace(/\n$/, "");
    return `${header}\r\n\r\n[file ${Buffer.byteLength(body, "latin1")}]`;
  }).join(token);
}

function readRequestPost(request, contentType) {
  let post = request.postData() || "";
  if (!post && typeof request.postDataBuffer === "function") {
    try {
      const buf = request.postDataBuffer();
      if (buf && buf.length) post = Buffer.isBuffer(buf) ? buf.toString("latin1") : String(buf);
    } catch {
      post = "";
    }
  }
  if (post && /name="/.test(post) && /multipart\/form-data/i.test(contentType)) {
    return redactMultipartFiles(post, contentType);
  }
  return post;
}

function markMultipartUnread(item) {
  if (/multipart\/form-data/i.test(item.request_content_type || "") && !item.post_data) {
    item.post_data_unreadable = "multipart";
  }
}

export function attachNetwork(recordingId, context) {
  const state = bag(recordingId);
  context.on("request", (request) => {
    const type = request.resourceType();
    if (type !== "xhr" && type !== "fetch") return;
    if (NOISE.test(request.url())) return;
    const requestHeaders = request.headers();
    noteRequestHeaders(recordingId, requestHeaders);
    state.seq += 1;
    const id = `req_${state.items.length + 1}`;
    const started = Date.now();
    const contentType = String(requestHeaders["content-type"] || "");
    const item = {
      id,
      method: request.method(),
      url: request.url(),
      path: new URL(request.url()).pathname,
      query: new URL(request.url()).search.replace(/^\?/, ""),
      post_data: readRequestPost(request, contentType),
      request_headers: pickCredentialHeaders(requestHeaders),
      request_content_type: contentType,
      request_header_names: Object.keys(requestHeaders),
      status: 0,
      response_body: null,
      body_missing: true,
      started_at: started,
      ended_at: 0,
      action_id: "",
      frame: request.frame(),
      content_type: "",
    };
    markMultipartUnread(item);
    const action = state.action;
    if (action?.open && started >= action.started && (action.anyFrame || (action.frame && item.frame === action.frame))) {
      item.action_id = action.id;
    }
    state.items.push(item);
    request._cabpId = id;
    const applyHeaders = (raw) => {
      const merged = { ...requestHeaders, ...(raw && typeof raw === "object" ? raw : {}) };
      item.request_headers = mergePackedHeaders(item.request_headers, pickCredentialHeaders(merged));
      noteRequestHeaders(recordingId, merged);
      const ctype = String(merged["content-type"] || merged["Content-Type"] || "");
      if (ctype) item.request_content_type = item.request_content_type || ctype;
      markMultipartUnread(item);
    };
    Promise.resolve().then(async () => {
      try {
        if (typeof request.headersArray === "function") {
          const rows = await request.headersArray();
          const obj = {};
          for (const row of rows || []) {
            if (row?.name) obj[row.name] = row.value;
          }
          applyHeaders(obj);
        }
      } catch {
        // headersArray 不是所有协议都有
      }
      try {
        const all = typeof request.allHeaders === "function" ? await request.allHeaders() : {};
        applyHeaders(all);
      } catch {
        // allHeaders 可能被浏览器藏掉
      }
    });
  });
  context.on("response", async (response) => {
    const request = response.request();
    const item = state.items.find((row) => row.id === request._cabpId);
    if (!item) return;
    item.status = response.status();
    item.ended_at = Date.now();
    item.content_type = response.headers()["content-type"] || "";
    const action = state.action;
    if (!item.action_id && action?.open && item.started_at >= action.started && (action.anyFrame || (action.frame && item.frame === action.frame))) {
      item.action_id = action.id;
    }
    try {
      if (!TEXT_TYPE.test(item.content_type)) {
        const headers = response.headers();
        const disposition = headers["content-disposition"] || "";
        const raw = disposition.match(/filename\*=(?:UTF-8'')?([^;]+)/i)?.[1] || disposition.match(/filename="?([^";]+)"?/i)?.[1] || "";
        let filename = raw.trim();
        try { filename = decodeURIComponent(filename); } catch { /* 保留原样 */ }
        let size = Number(headers["content-length"] || 0);
        if (!size) size = (await response.body().catch(() => Buffer.alloc(0))).length;
        item.response_body = null;
        item.body_missing = false;
        item.response_binary = { content_type: item.content_type, size, filename };
      } else {
        item.response_body = await response.text();
        item.body_missing = false;
        noteLoginBody(recordingId, item.response_body, { method: item.method, url: item.url, headers: item.request_headers });
      }
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
      post_data_unreadable: item.post_data_unreadable || undefined,
      request_content_type: item.request_content_type || "",
      request_header_names: item.request_header_names || [],
      status: item.status,
      response_body: item.response_body,
      body_missing: item.body_missing,
      started_at: item.started_at,
      ended_at: item.ended_at,
      action_id: item.action_id,
      content_type: item.content_type,
      ...(item.response_binary ? { response_binary: item.response_binary } : {}),
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

function responseKeysOf(row) {
  if (row._responseKeys) return row._responseKeys;
  const body = row.response_body;
  if (body == null || row.body_missing) {
    row._responseKeys = [];
    return row._responseKeys;
  }
  if (Buffer.byteLength(String(body)) > KEY_PARSE_BYTES) {
    row._responseKeys = ["_omitted"];
    return row._responseKeys;
  }
  row._responseKeys = responseKeyPreview(body);
  return row._responseKeys;
}

export function compactNetworkIndex(rows) {
  return (rows || []).map((row) => ({
    id: row.id,
    method: row.method,
    path: row.path,
    status: row.status,
    action_id: row.action_id || "",
    keys: row.keys || [],
    ...(row.response_keys?.length ? { response_keys: row.response_keys } : {}),
    ...(row.body_missing ? { body_missing: true } : {}),
    ...(row.issues_credential ? { issues_credential: true } : {}),
    ...(row.empty ? { empty: row.empty } : {}),
    ...(row.response_binary ? { binary: row.response_binary } : {}),
    ...(row.binary ? { binary: row.binary } : {}),
    ...(row.after_action ? { after_action: row.after_action, after_ms: row.after_ms } : {}),
    ...(row.post_data_unreadable ? { post_data_unreadable: row.post_data_unreadable } : {}),
  }));
}

export function networkBodyForModel(item) {
  if (!item) return { ok: false, error: "missing" };
  const body = item.response_body;
  const bytes = body == null ? 0 : Buffer.byteLength(String(body));
  if (bytes > BODY_OMIT_BYTES) {
    const { response_body: _drop, ...rest } = item;
    return {
      ...rest,
      response_body_omitted: true,
      response_body_bytes: bytes,
      response_keys: responseKeyPreview(body),
      hint: "正文过大已省略。用 evidence_get 同 id 取全文。",
    };
  }
  return item;
}

export function waitForAction(recordingId, actionId, timeout = 1500) {
  const state = bag(recordingId);
  const started = Date.now();
  return new Promise((resolve) => {
    const tick = () => {
      const pending = state.items.some((row) => row.action_id === actionId && !row.ended_at);
      if (!pending || Date.now() - started >= timeout) resolve();
      else setTimeout(tick, 50);
    };
    tick();
  });
}

export function listNetwork(recordingId, { after_id = "", action_id = "", all = false } = {}) {
  let rows = bag(recordingId).items;
  if (after_id) {
    const at = rows.findIndex((row) => row.id === after_id);
    rows = at >= 0 ? rows.slice(at + 1) : rows;
  }
  if (action_id) rows = rows.filter((row) => row.action_id === action_id);
  if (!all) rows = foldNetwork(rows).kept;
  return rows.map((row) => {
    const index = requestIndexRow(row);
    const responseKeys = responseKeysOf(row);
    const prior = row.action_id ? null : [...bag(recordingId).actions].reverse().find((act) => act.started <= row.started_at);
    return {
      id: row.id,
      method: row.method,
      path: row.path,
      status: row.status,
      body_bytes: row.response_body == null ? 0 : Buffer.byteLength(String(row.response_body)),
      body_missing: row.body_missing,
      action_id: row.action_id,
      keys: index.keys,
      ...(responseKeys.length ? { response_keys: responseKeys } : {}),
      ...(index.empty ? { empty: index.empty } : {}),
      ...(index.issues_credential ? { issues_credential: true } : {}),
      ...(row._repeat ? { repeat: row._repeat } : {}),
      ...(row.response_binary ? { binary: row.response_binary } : {}),
      ...(prior ? { after_action: prior.id, after_ms: row.started_at - prior.started } : {}),
      ...(row.post_data_unreadable ? { post_data_unreadable: row.post_data_unreadable } : {}),
    };
  });
}

export function requestKeyIndex(recordingId) {
  return bag(recordingId).items.map((row) => requestIndexRow(row));
}

function requestAuthFailed(item) {
  if (Number(item?.status) === 401) return true;
  if (Number(item?.status) >= 400) return true;
  const body = String(item?.response_body || "").slice(0, 800);
  return /"code"\s*:\s*40[13]\b/.test(body);
}

function applyCredentialHeaders(state, headers) {
  const usable = pickCredentialHeaders(headers);
  if (!Object.keys(usable).length) return;
  state.cdpHeaders = mergePackedHeaders(state.cdpHeaders, usable);
  const now = Date.now();
  for (let i = state.items.length - 1; i >= 0; i -= 1) {
    const item = state.items[i];
    if (item.ended_at && now - item.ended_at > 4000) continue;
    item.request_headers = mergePackedHeaders(item.request_headers, usable);
    return;
  }
}

export function pickLatestCredentialHeaders(items, cdpHeaders) {
  let fallback = usableAuthHeaders(cdpHeaders);
  for (let i = (items || []).length - 1; i >= 0; i -= 1) {
    if (requestAuthFailed(items[i])) continue;
    const headers = usableAuthHeaders(items[i].request_headers);
    if (!Object.keys(headers).length) continue;
    if (hasAccessTokenHeader(headers)) return headers;
    if (!hasAccessTokenHeader(fallback)) fallback = headers;
  }
  return fallback;
}

export function latestCredentialHeaders(recordingId) {
  const state = bag(recordingId);
  return pickLatestCredentialHeaders(state.items, state.cdpHeaders);
}

export function attachCredentialCapture(recordingId, context, page) {
  if (!context || !page || typeof context.newCDPSession !== "function") return;
  const state = bag(recordingId);
  context.newCDPSession(page).then(async (session) => {
    await session.send("Network.enable");
    session.on("Network.requestWillBeSent", (params) => {
      const kind = String(params?.type || "");
      if (kind && kind !== "XHR" && kind !== "Fetch") return;
      if (NOISE.test(params?.request?.url || "")) return;
      applyCredentialHeaders(state, params?.request?.headers);
      noteRequestHeaders(recordingId, params?.request?.headers);
    });
    session.on("Network.requestWillBeSentExtraInfo", (params) => {
      applyCredentialHeaders(state, params?.headers);
      noteRequestHeaders(recordingId, params?.headers);
    });
  }).catch(() => {});
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
    ...(item.post_data_unreadable ? { post_data_unreadable: item.post_data_unreadable } : {}),
    request_content_type: item.request_content_type || "",
    request_header_names: item.request_header_names || [],
    status: item.status,
    response_body: item.response_body,
    body_missing: item.body_missing,
    started_at: item.started_at,
    ended_at: item.ended_at,
    action_id: item.action_id,
    content_type: item.content_type,
    ...(item.response_binary ? { response_binary: item.response_binary } : {}),
  };
}
