/**
 * 无分类事实：动作台账和请求形状。时间接近只构成候选。
 */

import { isNoiseNetworkPath } from "./browser-actions.mjs";

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function compactUrl(raw) {
  const text = String(raw || "");
  if (!text) return "";
  try {
    const url = new URL(text);
    return `${url.pathname}${url.search}`;
  } catch {
    return text;
  }
}

function flattenKeys(value, prefix, into) {
  if (value == null) return;
  if (Array.isArray(value)) {
    if (!value.length) {
      into.push({ key: prefix.split(".").at(-1) || prefix, path: prefix, kind: "array" });
      return;
    }
    flattenKeys(value[0], prefix, into);
    return;
  }
  if (typeof value !== "object") {
    into.push({
      key: prefix.split(".").at(-1) || prefix,
      path: prefix,
      kind: typeof value,
    });
    return;
  }
  const keys = Object.keys(value);
  if (!keys.length) {
    into.push({ key: prefix.split(".").at(-1) || prefix, path: prefix, kind: "object" });
    return;
  }
  for (const key of keys) {
    flattenKeys(value[key], prefix ? `${prefix}.${key}` : key, into);
  }
}

function parseBody(payload) {
  const body = asRecord(payload.body);
  const text = typeof body.text === "string" ? body.text.trim() : "";
  if (text) {
    try {
      return JSON.parse(text);
    } catch {
      if (text.includes("=") && !text.startsWith("{") && !text.startsWith("[")) {
        try {
          return Object.fromEntries(new URLSearchParams(text).entries());
        } catch {
          return null;
        }
      }
      return null;
    }
  }
  if (body.json && typeof body.json === "object") return body.json;
  return null;
}

export function compactPathname(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  try {
    return new URL(text, "http://local.test").pathname;
  } catch {
    return text.split("?")[0];
  }
}

function parseQuery(url) {
  try {
    const parsed = new URL(String(url || ""), "http://local.test");
    return Object.fromEntries(parsed.searchParams.entries());
  } catch {
    return {};
  }
}

export function buildActionTimeline(events) {
  const rows = [];
  const list = Array.isArray(events) ? events : [];
  for (let i = 0; i < list.length; i += 1) {
    const event = list[i];
    const payload = asRecord(event?.payload);
    const kind = String(event?.kind || "");
    if (kind !== "interaction") continue;
    const action = String(payload.kind || "");
    if (/pointer|mousemove|mouseover|mouseenter|mouseleave/i.test(action)) continue;
    const requests = [];
    for (let j = i + 1; j < list.length; j += 1) {
      const next = list[j];
      if (next.kind === "interaction") break;
      if (next.kind !== "network_request") continue;
      const type = String(next.payload?.resource_type || "");
      if (type && type !== "xhr" && type !== "fetch") continue;
      requests.push({
        seq: next.seq,
        method: String(next.payload?.method || ""),
        path: compactUrl(next.payload?.url || next.payload?.path),
        request_id: String(next.payload?.request_id || ""),
      });
    }
    rows.push({
      seq: event.seq,
      actor: String(payload.actor || "human"),
      action,
      label: String(payload.label || payload.text || payload.name || payload.tag || ""),
      requests,
    });
  }
  return { count: rows.length, actions: rows };
}

export function requestShapeFromEvents(events, seq) {
  const list = Array.isArray(events) ? events : [];
  const target = Number(seq) || 0;
  const event = list.find((item) => Number(item.seq) === target);
  if (!event) return { found: false, seq: target };
  const payload = asRecord(event.payload);
  const query = parseQuery(payload.url);
  const body = parseBody(payload);
  const queryKeys = [];
  const bodyKeys = [];
  flattenKeys(query, "query", queryKeys);
  if (body && typeof body === "object") flattenKeys(body, "body", bodyKeys);
  return {
    found: true,
    seq: event.seq,
    method: String(payload.method || ""),
    path: compactUrl(payload.url || payload.path),
    request_id: String(payload.request_id || ""),
    query_keys: queryKeys,
    body_keys: bodyKeys,
  };
}

export function collectShapePaths(shape) {
  const paths = new Set();
  for (const item of [...(shape?.query_keys || []), ...(shape?.body_keys || [])]) {
    if (item?.path) paths.add(String(item.path));
    if (item?.key) paths.add(String(item.key));
  }
  return paths;
}

export function pathExistsInShape(paramPath, shape) {
  const want = String(paramPath || "").trim();
  if (!want) return false;
  const paths = collectShapePaths(shape);
  if (paths.has(want)) return true;
  if ([...paths].some((item) => item.startsWith(`${want}.`))) return true;
  if (!want.includes(".")) {
    return [...paths].some((item) => item.endsWith(`.${want}`));
  }
  const parts = want.split(".");
  if (parts.length >= 3) {
    const parent = parts.slice(0, -1).join(".");
    if (parent !== "query" && parent !== "body" && paths.has(parent)) return true;
  }
  return false;
}

export function resolveExecuteRequest(events, { method = "", path = "", seq = 0, request_id = "" } = {}) {
  const list = Array.isArray(events) ? events : [];
  const targetSeq = Number(seq) || 0;
  if (targetSeq) {
    const hit = list.find((item) => Number(item.seq) === targetSeq && item.kind === "network_request");
    if (hit) return hit;
  }
  const requestId = String(request_id || "").trim();
  if (requestId) {
    const hit = list.find((item) => (
      item.kind === "network_request" && String(item.payload?.request_id || "") === requestId
    ));
    if (hit) return hit;
  }
  const wantMethod = String(method || "").toUpperCase();
  const wantPath = compactPathname(path);
  if (!wantMethod && !wantPath) return null;
  const matches = list.filter((item) => {
    if (item.kind !== "network_request") return false;
    const type = String(item.payload?.resource_type || "");
    if (type && type !== "xhr" && type !== "fetch") return false;
    if (wantMethod && String(item.payload?.method || "").toUpperCase() !== wantMethod) return false;
    if (wantPath && compactPathname(item.payload?.url || item.payload?.path) !== wantPath) return false;
    return true;
  });
  return matches.at(-1) || null;
}

function requestFactForStep(result, stepId) {
  const requests = result?.request_facts?.requests;
  if (!Array.isArray(requests)) return null;
  return requests.find((item) => String(item?.step_id || "").trim() === stepId) || null;
}

export function callerParamsForExecute(result, capability) {
  const steps = Array.isArray(result?.steps) ? result.steps : [];
  const refs = Array.isArray(capability?.request_refs) ? capability.request_refs : [];
  const execute = refs.find((ref) => asRecord(ref).usage === "execute" || !ref?.usage);
  if (!execute) return [];
  const stepId = String(execute.step_id || "").trim();
  const step = steps.find((item) => String(item?.step_id || "").trim() === stepId) || null;
  const params = Array.isArray(step?.params) ? step.params : [];
  return params
    .filter((param) => param?.exposed_to_user === true && String(param.key || "").trim())
    .map((param) => ({
      key: String(param.key).trim(),
      path: String(param.path || "").trim(),
      step,
      execute,
    }));
}

export function findExecuteEvidenceGaps(result, events) {
  const gaps = [];
  const capabilities = Array.isArray(result?.capabilities) ? result.capabilities : [];
  for (const capability of capabilities) {
    const callers = callerParamsForExecute(result, capability);
    const refs = Array.isArray(capability?.request_refs) ? capability.request_refs : [];
    const execute = refs.find((ref) => asRecord(ref).usage === "execute" || !ref?.usage);
    if (!execute) continue;
    const stepId = String(execute.step_id || "").trim();
    const step = (Array.isArray(result?.steps) ? result.steps : [])
      .find((item) => String(item?.step_id || "").trim() === stepId) || {};
    const fact = requestFactForStep(result, stepId);
    const event = resolveExecuteRequest(events, {
      method: execute.method || step.method || fact?.method,
      path: execute.path || step.path || fact?.path || fact?.url,
      seq: execute.seq || fact?.seq,
      request_id: execute.request_id || fact?.request_id,
    });
    if (!event) {
      gaps.push({
        capability_id: String(capability.capability_id || ""),
        step_id: stepId,
        reason: "missing_execute",
      });
      continue;
    }
    const shape = requestShapeFromEvents(events, event.seq);
    const schemaKeys = Object.keys(asRecord(capability?.input_schema?.properties));
    const wanted = [
      ...callers,
      ...schemaKeys
        .filter((key) => !callers.some((item) => item.key === key))
        .map((key) => {
          const param = callers.find((item) => item.key === key);
          return { key, path: param?.path || "" };
        }),
    ];
    for (const item of wanted) {
      const path = item.path || (item.key.includes(".") ? item.key : "");
      const found = path
        ? pathExistsInShape(path, shape)
        : pathExistsInShape(item.key, shape) || pathExistsInShape(`query.${item.key}`, shape) || pathExistsInShape(`body.${item.key}`, shape);
      if (!found) {
        gaps.push({
          capability_id: String(capability.capability_id || ""),
          step_id: stepId,
          key: item.key,
          path: path || item.key,
          reason: "missing_key",
        });
      }
    }
  }
  return gaps;
}

export function isFinalStale(events, acceptedLastSeq = 0) {
  const cursor = Number(acceptedLastSeq) || 0;
  const list = Array.isArray(events) ? events : [];
  return list.some((event) => {
    if (Number(event.seq) <= cursor) return false;
    if (event.kind === "interaction") {
      if (String(event.payload?.actor || "") !== "human") return false;
      const action = String(event.payload?.kind || "");
      return !/pointer_move|mousemove|mouseover|mouseenter/i.test(action);
    }
    if (event.kind === "network_request") {
      const type = String(event.payload?.resource_type || "");
      if (type && type !== "xhr" && type !== "fetch") return false;
      return !isNoiseNetworkPath(event.payload?.url || event.payload?.path || "");
    }
    return false;
  });
}
