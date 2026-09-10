/**
 * 只按合同投影请求。缺键失败，绝不补键、不猜 now、不猜公式。
 * 系统常量按能力已给出的 default_value / 合同理由取值，不把用户样本写成常量。
 */

import { resolveSystemDefault } from "./skill-export/contract-materialize.mjs";


function isPlainObject(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function asList(value) {
  return Array.isArray(value) ? value : [];
}

function setPath(target, rawPath, value) {
  const text = String(rawPath || "").trim();
  const parts = text.replace(/^(query|body)\./, "").split(".").filter(Boolean);
  if (!parts.length) return;
  let cursor = target;
  for (let index = 0; index < parts.length - 1; index += 1) {
    const key = parts[index];
    if (!isPlainObject(cursor[key])) cursor[key] = {};
    cursor = cursor[key];
  }
  cursor[parts[parts.length - 1]] = value;
}

function pathRoot(rawPath) {
  const text = String(rawPath || "").trim();
  if (text.startsWith("query.")) return "query";
  return "body";
}

export function projectContractToRequest(draft, capabilityId, inputs = {}) {
  const result = isPlainObject(draft) ? draft : {};
  const id = String(capabilityId || "").trim();
  const capability = asList(result.capabilities).find((item) => String(item?.capability_id || "") === id);
  if (!capability) {
    return { ok: false, missing: [], extra: [], error: `找不到能力 ${id}` };
  }
  const stepsById = new Map(
    asList(result.steps).map((step) => [String(step?.step_id || "").trim(), step]),
  );
  const refs = asList(capability.request_refs);
  const execute = refs.find((ref) => (ref?.usage || "execute") === "execute");
  const step = stepsById.get(String(execute?.step_id || "").trim());
  if (!step) {
    return { ok: false, missing: [], extra: [], error: "找不到 execute step" };
  }
  const given = isPlainObject(inputs) ? inputs : {};
  const givenKeys = new Set(Object.keys(given));
  const missing = [];
  const query = {};
  const body = {};
  const used = new Set();
  for (const param of asList(step.params)) {
    const key = String(param?.key || "").trim();
    const wirePath = String(param?.path || "").trim();
    if (!key || !wirePath) continue;
    if (param.exposed_to_user === true) {
      if (!(key in given)) {
        if (param.required === true) missing.push(key);
        continue;
      }
      used.add(key);
      setPath(pathRoot(wirePath) === "query" ? query : body, wirePath, given[key]);
      continue;
    }
    const resolved = resolveSystemDefault(param);
    if (resolved.has) {
      setPath(pathRoot(wirePath) === "query" ? query : body, wirePath, resolved.value);
    }
  }
  const extra = [...givenKeys].filter((key) => !used.has(key));
  if (missing.length) {
    return {
      ok: false,
      capability_id: id,
      missing,
      extra,
      error: `合同缺键: ${missing.join(", ")}`,
    };
  }
  return {
    ok: true,
    capability_id: id,
    method: step.method || "",
    path: step.path || "",
    query,
    body,
    missing: [],
    extra,
  };
}

export function writeCapabilitiesUnresolved(draft) {
  const unresolved = asList(draft?.unresolved).map((item) => String(item?.capability_id || item?.id || item || ""));
  return asList(draft?.capabilities)
    .filter((item) => String(item?.kind || "") !== "query")
    .filter((item) => unresolved.includes(String(item?.capability_id || "")));
}
