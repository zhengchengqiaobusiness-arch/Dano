import type { CapabilityContract, InputFormField } from "../domain.js";
import { randomUUID } from "node:crypto";
import { getByPath, setByPath } from "../utils.js";
import { dateToMillis, isDateInput, normalizeDateString } from "../inference/date-format.js";

function fieldValue(cap: CapabilityContract, input: Record<string, unknown>, name: string) {
  const field = cap.inputForm.find(item => item.name === name);
  return { value: getByPath(input, field?.path || `$.${name}`), path: field?.path || `$.${name}` };
}

function expandUrl(cap: CapabilityContract, input: Record<string, unknown>) {
  const template = cap.transport.urlTemplate;
  const url = new URL(template);
  const consumed = new Set<string>();

  url.pathname = url.pathname.replace(/\{([^}]+)\}/g, (_, key) => {
    const resolved = fieldValue(cap, input, key);
    const value = resolved.value;
    if (value === undefined) throw new Error(`Missing URL parameter: ${key}`);
    consumed.add(resolved.path);
    return encodeURIComponent(String(value));
  });

  for (const [key, value] of [...url.searchParams]) {
    const match = value.match(/^\{([^}]+)\}$/);
    if (!match) continue;
    const field = match[1]!;
    const resolved = fieldValue(cap, input, field);
    const actual = resolved.value;
    if (actual === undefined) {
      url.searchParams.delete(key);
      continue;
    }
    consumed.add(resolved.path);
    url.searchParams.set(key, String(actual));
  }

  return { url, consumed };
}

function resolveRule(rule: string) {
  if (rule.startsWith("literal:")) {
    const raw = rule.slice("literal:".length);
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }
  if (rule.startsWith("env:")) {
    const name = rule.slice("env:".length);
    if (!process.env[name]) throw new Error(`Missing environment value: ${name}`);
    return process.env[name];
  }
  if (rule === "uuid") return randomUUID();
  if (rule === "now:iso") return new Date().toISOString();
  throw new Error(`Unsupported field resolution rule: ${rule}`);
}

function itemInputKey(field: { path: string }) {
  return field.path.replace(/^\$\./, "").replace(/\[\*\]/g, "");
}

function evalComputed(expr: string, prepared: Record<string, unknown>, item?: Record<string, unknown>) {
  const scope = { ...prepared, ...(item || {}) };
  const withSum = expr.replace(/sum\(items\.([A-Za-z_][A-Za-z0-9_]*)\)/g, (_, name) => {
    const items = prepared.items;
    if (!Array.isArray(items)) throw new Error(`无法计算 sum(items.${name})`);
    return String((items as Array<Record<string, unknown>>).reduce((sum, row) => sum + Number(row?.[name] || 0), 0));
  });
  const text = withSum.replace(/[A-Za-z_][A-Za-z0-9_]*/g, name => {
    const value = scope[name];
    if (value === undefined || value === null) throw new Error(`计算缺少字段：${name}`);
    return String(value);
  });
  if (!/^[\d.\seE+\-*/()]+$/.test(text)) throw new Error(`非法计算公式：${expr}`);
  return Function(`"use strict"; return (${text})`)();
}

function resolveFieldRule(field: CapabilityContract["inputForm"][number], prepared: Record<string, unknown>, item?: Record<string, unknown>) {
  const rule = field.defaultRule || "";
  if (rule.startsWith("copy:")) {
    const name = rule.slice("copy:".length);
    if (item && item[name] !== undefined) return item[name];
    return prepared[name];
  }
  if (rule.startsWith("computed:")) return evalComputed(rule.slice("computed:".length), prepared, item);
  if (rule.startsWith("from:")) return undefined;
  if (rule) return resolveRule(rule);
  return undefined;
}

function literalKey(jsonPath: string) {
  if (jsonPath === "$") return undefined;
  const literal = jsonPath.replace(/^\$\./, "");
  return literal && !literal.includes(".") ? literal : undefined;
}

function applyCandidate(field: InputFormField, value: unknown) {
  const rule = field.candidates;
  if (!rule || rule.type !== "static" || value === undefined || value === null) return value;
  for (const option of rule.values) {
    if (option.value === value || String(option.label) === String(value)) return option.value;
  }
  return value;
}

function coerceFieldValue(value: unknown, field: InputFormField) {
  if (value === undefined || value === null) return value;
  let next = applyCandidate(field, value);
  if (typeof next === "string" && isDateInput(next.trim())) {
    if (field.valueType === "integer" || field.valueType === "number") return dateToMillis(next);
    if (field.valueType === "string") return normalizeDateString(next, field.dateClock);
  }
  if (field.valueType === "string") return typeof next === "string" ? next : String(next);
  if (field.valueType === "integer") {
    if (typeof next === "boolean") return next;
    if (typeof next === "number" && Number.isInteger(next)) return next;
    if (typeof next === "string" && /^[-+]?\d+$/.test(next.trim())) return Number.parseInt(next, 10);
  }
  if (field.valueType === "number" && typeof next === "string" && /^[-+]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(next.trim())) {
    return Number(next);
  }
  if (field.valueType === "boolean" && typeof next === "string" && /^(true|false)$/i.test(next)) {
    return next.toLowerCase() === "true";
  }
  if (field.valueType === "array" && !Array.isArray(next)) return [next];
  return next;
}

function hoistNamedFields(cap: CapabilityContract, supplied: Record<string, unknown>) {
  const prepared = structuredClone(supplied);
  const itemNames = new Set(cap.inputForm.filter(field => field.path.includes("[*]")).map(field => field.name));
  for (const field of cap.inputForm) {
    if (!field.name || field.path.includes("[*]") || itemNames.has(field.name) || !Object.prototype.hasOwnProperty.call(prepared, field.name)) continue;
    if (getByPath(prepared, field.path) !== undefined) continue;
    if (literalKey(field.path) === field.name) continue;
    const value = prepared[field.name];
    delete prepared[field.name];
    setByPath(prepared, field.path, value);
  }
  return prepared;
}

function nestLineItems(cap: CapabilityContract, supplied: Record<string, unknown>) {
  const prepared = structuredClone(supplied);
  const itemFields = cap.inputForm.filter(field => field.path.includes("[*]"));
  if (!itemFields.length) return prepared;
  if (Array.isArray(prepared.items)) return prepared;
  const headerNames = new Set(cap.inputForm.filter(field => !field.path.includes("[*]")).map(field => field.name));
  const item: Record<string, unknown> = {};
  for (const field of itemFields) {
    const dotted = itemInputKey(field);
    if (dotted !== field.name && Object.prototype.hasOwnProperty.call(prepared, dotted)) {
      item[field.name] = prepared[dotted];
      delete prepared[dotted];
    } else if (Object.prototype.hasOwnProperty.call(prepared, field.name) && !headerNames.has(field.name)) {
      item[field.name] = prepared[field.name];
      delete prepared[field.name];
    }
  }
  if (Object.keys(item).length) prepared.items = [item];
  return prepared;
}

function coercePresentFields(cap: CapabilityContract, prepared: Record<string, unknown>, requireMissing: boolean) {
  for (const field of cap.inputForm) {
    if (field.path.includes("[*]")) {
      const [prefix, suffix] = field.path.split("[*].");
      const items = getByPath(prepared, prefix || "");
      if (!Array.isArray(items)) {
        if (requireMissing && field.required && field.source === "caller") {
          throw new Error(`Missing caller field: ${field.label} (${field.path})`);
        }
        continue;
      }
      const name = (suffix || "").split(".").pop() || field.name;
      for (const row of items) {
        if (!row || typeof row !== "object" || Array.isArray(row)) continue;
        const current = row as Record<string, unknown>;
        const value = current[name];
        if (value === undefined) {
          if (requireMissing && field.required && field.source === "caller") {
            throw new Error(`Missing caller field: ${field.label} (${field.path})`);
          }
          continue;
        }
        current[name] = coerceFieldValue(value, field);
      }
      continue;
    }
    const value = getByPath(prepared, field.path);
    if (value === undefined) {
      if (!requireMissing) continue;
      if (field.required) {
        if (field.source === "caller") throw new Error(`Missing caller field: ${field.label} (${field.path})`);
        throw new Error(`Required system field is unresolved: ${field.label} (${field.path})`);
      }
      continue;
    }
    setByPath(prepared, field.path, coerceFieldValue(value, field));
  }
}

function prepareInput(cap: CapabilityContract, input: Record<string, unknown>) {
  const prepared = hoistNamedFields(cap, nestLineItems(cap, input));
  coercePresentFields(cap, prepared, false);
  let changed = true;
  while (changed) {
    changed = false;
    for (const field of cap.inputForm) {
      if (!field.defaultRule) continue;
      if (field.path.includes("[*]")) {
        const [prefix, suffix] = field.path.split("[*].");
        const items = getByPath(prepared, prefix || "");
        if (!Array.isArray(items)) continue;
        const name = (suffix || "").split(".").pop() || field.name;
        for (const row of items) {
          if (!row || typeof row !== "object" || Array.isArray(row)) continue;
          const current = row as Record<string, unknown>;
          if (current[name] !== undefined) continue;
          try {
            const value = resolveFieldRule(field, prepared, current);
            if (value !== undefined) {
              current[name] = coerceFieldValue(value, field);
              changed = true;
            }
          } catch {
            // wait for another field in a later pass
          }
        }
        continue;
      }
      if (getByPath(prepared, field.path) !== undefined) continue;
      try {
        const value = resolveFieldRule(field, prepared);
        if (value !== undefined) {
          setByPath(prepared, field.path, coerceFieldValue(value, field));
          changed = true;
        }
      } catch {
        // wait for another field in a later pass
      }
    }
  }
  coercePresentFields(cap, prepared, true);
  return prepared;
}

function deleteByPath(target: Record<string, unknown>, jsonPath: string) {
  const parts = jsonPath.replace(/^\$\./, "").split(".").filter(Boolean);
  let current: any = target;
  for (const part of parts.slice(0, -1)) current = current?.[part];
  if (current && parts.length) delete current[parts.at(-1)!];
}

function authHeaders() {
  const raw = process.env.SKILL_AUTH_HEADERS || "{}";
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("SKILL_AUTH_HEADERS must be a JSON object");
  }
  return parsed as Record<string, string>;
}

export function materializeHttpRequest(cap: CapabilityContract, input: Record<string, unknown>) {
  const prepared = prepareInput(cap, input);
  const { url, consumed } = expandUrl(cap, prepared);
  const method = cap.transport.method.toUpperCase();
  if (!["GET", "HEAD"].includes(method)) {
    const body = structuredClone(prepared);
    for (const fieldPath of consumed) deleteByPath(body, fieldPath);
    return { method, url: url.toString(), query: undefined as Record<string, string> | undefined, body, prepared };
  }
  for (const field of cap.inputForm) {
    const value = getByPath(prepared, field.path);
    if (!consumed.has(field.path) && value !== undefined && typeof value !== "object") {
      url.searchParams.set(field.name, String(value));
    }
  }
  return {
    method,
    url: url.toString(),
    query: Object.fromEntries(url.searchParams.entries()),
    body: undefined as Record<string, unknown> | undefined,
    prepared
  };
}

export async function executeCapability(
  cap: CapabilityContract,
  input: Record<string, unknown>,
  confirmWrite = false
) {
  if (cap.validation.status !== "verified") throw new Error(`Capability ${cap.id} is not verified`);
  if (cap.confirmation.required && !confirmWrite) {
    throw new Error(`Capability ${cap.id} requires explicit write confirmation`);
  }

  const materialized = materializeHttpRequest(cap, input);
  const url = new URL(materialized.url);
  const method = materialized.method;
  const headers: Record<string, string> = {
    accept: "application/json",
    ...authHeaders()
  };

  const init: RequestInit = { method, headers };
  if (!["GET", "HEAD"].includes(method)) {
    headers["content-type"] = "application/json";
    init.body = JSON.stringify(materialized.body);
  }

  const response = await fetch(url, init);
  const text = await response.text();
  let body: unknown = text;
  try { body = JSON.parse(text); } catch {}

  const assertionResults = (cap.completion.assertions || []).map(assertion => {
    const actual = getByPath(body, assertion.path);
    const ok = assertion.kind === "exists" ? actual !== undefined
      : assertion.kind === "nonempty" ? actual !== undefined && actual !== null && actual !== "" && (!Array.isArray(actual) || actual.length > 0)
      : Object.is(actual, assertion.value);
    return { ...assertion, actual, ok };
  });
  const statusOk = cap.completion.acceptedHttpStatuses.includes(response.status);
  const ok = statusOk && assertionResults.every(a => a.ok);
  return {
    ok,
    status: response.status,
    body,
    assertions: assertionResults,
    capabilityId: cap.id
  };
}
