import type { CapabilityContract, InputFormField } from "../domain.js";
import { randomUUID } from "node:crypto";
import { getByPath, setByPath } from "../utils.js";
import { dateToMillis, isDateInput, normalizeDateString } from "../inference/date-format.js";
import { parseFromRule } from "../inference/field-derivation.js";
import { parseCollectionLeafPath } from "../inference/field-resolver.js";

export type MaterializeOptions = {
  catalog?: CapabilityContract[];
  lookupBodies?: Record<string, unknown>;
};

function sameJoin(left: unknown, right: unknown) {
  if (left == null || right == null || left === "") return false;
  return left === right || String(left) === String(right);
}

function extractJoined(body: unknown, jsonPath: string, viaValue?: unknown) {
  if (jsonPath.includes("[*].")) {
    const [prefix, suffix] = jsonPath.split("[*].");
    const rows = getByPath(body, prefix || "$");
    if (!Array.isArray(rows)) return undefined;
    const name = (suffix || "").split(".").pop() || "";
    const matched = rows.flatMap(row => {
      if (!row || typeof row !== "object" || Array.isArray(row)) return [];
      const record = row as Record<string, unknown>;
      if (viaValue !== undefined && viaValue !== null && viaValue !== ""
        && !sameJoin(record.id, viaValue) && !sameJoin(record.value, viaValue) && !sameJoin(record.code, viaValue)) {
        return [];
      }
      const value = record[name];
      return value === undefined ? [] : [value];
    });
    if (matched.length === 1) return matched[0];
    if (viaValue === undefined && matched.length && new Set(matched.map(item => String(item))).size === 1) return matched[0];
    return undefined;
  }
  return getByPath(body, jsonPath);
}

function resolveFrom(
  rule: string,
  prepared: Record<string, unknown>,
  item: Record<string, unknown> | undefined,
  options?: MaterializeOptions
) {
  const parsed = parseFromRule(rule);
  if (!parsed) return undefined;
  const body = options?.lookupBodies?.[parsed.capabilityId];
  if (body === undefined) return undefined;
  let viaValue: unknown = parsed.via ? item?.[parsed.via] : undefined;
  if (parsed.via && viaValue === undefined) viaValue = prepared[parsed.via];
  if (parsed.via && viaValue === undefined) return undefined;
  return extractJoined(body, parsed.fromPath, viaValue);
}

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
    if (Array.isArray(actual)) {
      url.searchParams.delete(key);
      for (const item of actual) url.searchParams.append(key, String(item));
    } else {
      url.searchParams.set(key, String(actual));
    }
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

function resolveFieldRule(
  field: CapabilityContract["inputForm"][number],
  prepared: Record<string, unknown>,
  item: Record<string, unknown> | undefined,
  options?: MaterializeOptions
) {
  const rule = field.defaultRule || "";
  if (rule.startsWith("copy:")) {
    const name = rule.slice("copy:".length);
    if (item && item[name] !== undefined) return item[name];
    return prepared[name];
  }
  if (rule.startsWith("computed:")) return evalComputed(rule.slice("computed:".length), prepared, item);
  if (rule.startsWith("from:")) {
    const resolved = resolveFrom(rule, prepared, item, options);
    if (resolved !== undefined) return resolved;
    const fallback = parseFromRule(rule)?.fallback;
    if (fallback === undefined) return undefined;
    try {
      return JSON.parse(fallback);
    } catch {
      return fallback;
    }
  }
  if (rule) return resolveRule(rule);
  return undefined;
}

function literalKey(jsonPath: string) {
  if (jsonPath === "$") return undefined;
  const literal = jsonPath.replace(/^\$\./, "");
  return literal && !literal.includes(".") ? literal : undefined;
}

function candidateValues(body: unknown, jsonPath: string) {
  if (!jsonPath.includes("[*]")) {
    const value = getByPath(body, jsonPath);
    return value === undefined ? [] : [value];
  }
  const [prefix, suffix] = jsonPath.split("[*]");
  const rows = getByPath(body, prefix || "$");
  if (!Array.isArray(rows)) return [];
  const childPath = suffix?.replace(/^\./, "");
  return rows.flatMap(row => {
    const value = childPath ? getByPath(row, `$.${childPath}`) : row;
    return value === undefined ? [] : [value];
  });
}

function applyCandidate(field: InputFormField, value: unknown, options?: MaterializeOptions) {
  const rule = field.candidates;
  if (!rule || value === undefined || value === null) return value;
  const optionsList = rule.type === "static"
    ? rule.values
    : (() => {
        const body = options?.lookupBodies?.[rule.capabilityId];
        if (body === undefined) return [];
        const values = candidateValues(body, rule.valuePath);
        const labels = candidateValues(body, rule.labelPath);
        return values.map((candidate, index) => ({ value: candidate, label: String(labels[index] ?? candidate) }));
      })();
  const convert = (item: unknown) => {
    const matches = optionsList.filter(option => sameJoin(option.value, item) || String(option.label) === String(item));
    if (matches.length === 1) return matches[0]!.value;
    return item;
  };
  if (Array.isArray(value)) return value.map(convert);
  for (const option of optionsList) {
    if (option.value === value || String(option.label) === String(value)) return option.value;
  }
  return value;
}

function coerceFieldValue(value: unknown, field: InputFormField, options?: MaterializeOptions) {
  if (value === undefined || value === null) return value;
  let next = applyCandidate(field, value, options);
  if (field.valueType === "array" && Array.isArray(next) && field.dateClocks?.length === next.length) {
    return next.map((item, index) =>
      typeof item === "string" && isDateInput(item.trim())
        ? normalizeDateString(item, field.dateClocks![index])
        : item
    );
  }
  if (typeof next === "string" && isDateInput(next.trim())) {
    if (field.valueType === "integer" || field.valueType === "number") return dateToMillis(next, field.dateClock);
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

function parseLiteralRule(rule?: string) {
  if (!rule?.startsWith("literal:")) return undefined;
  try {
    return JSON.parse(rule.slice("literal:".length));
  } catch {
    return undefined;
  }
}

function collectionTemplateRows(cap: CapabilityContract, prefix: string) {
  const parent = cap.inputForm.find(field => field.path === prefix);
  const value = parseLiteralRule(parent?.defaultRule);
  return Array.isArray(value) && value.every(item => item && typeof item === "object" && !Array.isArray(item))
    ? value as Record<string, unknown>[]
    : undefined;
}

function collectionFieldInputKeys(field: InputFormField, siblings: InputFormField[]) {
  const keys = new Set<string>([field.path, field.path.replace(/^\$\./, "")]);
  const clashes = siblings.filter(item => item.name === field.name);
  if (clashes.length <= 1) keys.add(field.name);
  return [...keys];
}

function applyCollectionTemplates(cap: CapabilityContract, prepared: Record<string, unknown>) {
  for (const field of cap.inputForm) {
    if (field.path.includes("[") && field.path !== field.name) {
      const parsed = parseCollectionLeafPath(field.path);
      if (parsed && parsed.index !== "*") continue;
    }
    const template = collectionTemplateRows(cap, field.path);
    if (!template?.length) continue;
    const current = getByPath(prepared, field.path);
    let rows = structuredClone(template);
    if (Array.isArray(current) && current.length) {
      rows = template.map((row, index) => {
        const overlay = current[index];
        if (!overlay || typeof overlay !== "object" || Array.isArray(overlay)) return structuredClone(row);
        return { ...structuredClone(row), ...overlay };
      });
      if (current.length > template.length) {
        rows.push(...current.slice(template.length).map(row => (
          row && typeof row === "object" && !Array.isArray(row) ? { ...row } : row
        )));
      }
    }
    const headerNames = new Set(
      cap.inputForm
        .filter(item => !parseCollectionLeafPath(item.path) && item.path !== field.path)
        .map(item => item.name)
    );
    for (const child of cap.inputForm) {
      const parsed = parseCollectionLeafPath(child.path);
      if (!parsed || parsed.prefix !== field.path) continue;
      for (const key of collectionFieldInputKeys(child, cap.inputForm)) {
        if (!Object.prototype.hasOwnProperty.call(prepared, key)) continue;
        const value = prepared[key];
        if (parsed.index === "*") {
          for (const row of rows) {
            if (Object.prototype.hasOwnProperty.call(row, parsed.key)) row[parsed.key] = value;
          }
        } else if (rows[parsed.index] && Object.prototype.hasOwnProperty.call(rows[parsed.index]!, parsed.key)) {
          rows[parsed.index]![parsed.key] = value;
        }
        if (key !== child.name || !headerNames.has(child.name)) delete prepared[key];
      }
    }
    for (const [key, value] of Object.entries(prepared)) {
      if (headerNames.has(key) || key === field.name) continue;
      if (!rows.some(row => Object.prototype.hasOwnProperty.call(row, key))) continue;
      for (const row of rows) {
        if (Object.prototype.hasOwnProperty.call(row, key)) row[key] = value;
      }
      delete prepared[key];
    }
    setByPath(prepared, field.path, rows);
  }
}

function nestLineItems(cap: CapabilityContract, supplied: Record<string, unknown>) {
  const prepared = structuredClone(supplied);
  const itemFields = cap.inputForm.filter(field => parseCollectionLeafPath(field.path));
  if (!itemFields.length) return prepared;
  if (Array.isArray(prepared.items)) return prepared;
  for (const field of itemFields) {
    for (const key of collectionFieldInputKeys(field, cap.inputForm)) {
      if (!Object.prototype.hasOwnProperty.call(prepared, key)) continue;
      if (getByPath(prepared, field.path) === undefined) setByPath(prepared, field.path, prepared[key]);
    }
  }
  return prepared;
}

function coercePresentFields(
  cap: CapabilityContract,
  prepared: Record<string, unknown>,
  requireMissing: boolean,
  options?: MaterializeOptions
) {
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
            const templates = collectionTemplateRows(cap, prefix || "");
            const index = items.indexOf(row);
            if (templates?.[index] && !Object.prototype.hasOwnProperty.call(templates[index], name)) continue;
            throw new Error(`Missing caller field: ${field.label} (${field.path})`);
          }
          continue;
        }
        current[name] = coerceFieldValue(value, field, options);
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
    setByPath(prepared, field.path, coerceFieldValue(value, field, options));
  }
}

function prepareInput(cap: CapabilityContract, input: Record<string, unknown>, options?: MaterializeOptions) {
  const prepared = hoistNamedFields(cap, nestLineItems(cap, input));
  applyCollectionTemplates(cap, prepared);
  coercePresentFields(cap, prepared, false, options);
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
        const templates = collectionTemplateRows(cap, prefix || "");
        items.forEach((row, index) => {
          if (!row || typeof row !== "object" || Array.isArray(row)) return;
          const current = row as Record<string, unknown>;
          if (current[name] !== undefined) return;
          if (templates?.[index] && !Object.prototype.hasOwnProperty.call(templates[index], name)) return;
          try {
            const value = resolveFieldRule(field, prepared, current, options);
            if (value !== undefined) {
              current[name] = coerceFieldValue(value, field, options);
              changed = true;
            }
          } catch {
            // wait for another field in a later pass
          }
        });
        continue;
      }
      if (getByPath(prepared, field.path) !== undefined) continue;
      try {
        const value = resolveFieldRule(field, prepared, undefined, options);
        if (value !== undefined) {
          setByPath(prepared, field.path, coerceFieldValue(value, field, options));
          changed = true;
        }
      } catch {
        // wait for another field in a later pass
      }
    }
  }
  coercePresentFields(cap, prepared, true, options);
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

export function materializeHttpRequest(
  cap: CapabilityContract,
  input: Record<string, unknown>,
  options?: MaterializeOptions
) {
  const prepared = prepareInput(cap, input, options);
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

async function lookupBodiesFor(
  cap: CapabilityContract,
  input: Record<string, unknown>,
  catalog: CapabilityContract[],
  visiting = new Set<string>()
): Promise<Record<string, unknown>> {
  const bodies: Record<string, unknown> = {};
  if (visiting.has(cap.id)) return bodies;
  visiting.add(cap.id);
  for (const field of cap.inputForm) {
    const parsed = parseFromRule(field.defaultRule || "");
    const candidateId = field.candidates?.type === "capability" ? field.candidates.capabilityId : undefined;
    for (const capabilityId of [...new Set([parsed?.capabilityId, candidateId].filter((item): item is string => Boolean(item)))]) {
      if (bodies[capabilityId] !== undefined || capabilityId === cap.id) continue;
      const source = catalog.find(item => item.id === capabilityId);
      if (!source) continue;
      const sourceInput = parsed?.capabilityId === capabilityId && parsed.via && input[parsed.via] !== undefined
        ? { [parsed.via]: input[parsed.via] }
        : input;
      const result = await executeCapability(source, sourceInput, false, catalog, visiting);
      bodies[capabilityId] = result.body;
    }
  }
  return bodies;
}

export async function executeCapability(
  cap: CapabilityContract,
  input: Record<string, unknown>,
  confirmWrite = false,
  catalog: CapabilityContract[] = [],
  visiting = new Set<string>()
) {
  if (cap.validation.status !== "verified") throw new Error(`Capability ${cap.id} is not verified`);
  if (cap.confirmation.required && !confirmWrite) {
    throw new Error(`Capability ${cap.id} requires explicit write confirmation`);
  }

  const lookupBodies = catalog.length ? await lookupBodiesFor(cap, input, catalog, visiting) : {};
  const materialized = materializeHttpRequest(cap, input, { catalog, lookupBodies });
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
