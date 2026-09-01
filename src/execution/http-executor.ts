import type { CapabilityContract } from "../domain.js";
import { randomUUID } from "node:crypto";
import { getByPath, setByPath } from "../utils.js";

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
  if (rule.startsWith("literal:")) return JSON.parse(rule.slice("literal:".length));
  if (rule.startsWith("env:")) {
    const name = rule.slice("env:".length);
    if (!process.env[name]) throw new Error(`Missing environment value: ${name}`);
    return process.env[name];
  }
  if (rule === "uuid") return randomUUID();
  if (rule === "now:iso") return new Date().toISOString();
  throw new Error(`Unsupported field resolution rule: ${rule}`);
}

function prepareInput(cap: CapabilityContract, input: Record<string, unknown>) {
  const prepared = structuredClone(input);
  for (const field of cap.inputForm) {
    let value = getByPath(prepared, field.path);
    if (value === undefined && field.defaultRule) {
      value = resolveRule(field.defaultRule);
      setByPath(prepared, field.path, value);
    }
    if (value === undefined && field.required) {
      if (field.source === "caller") throw new Error(`Missing caller field: ${field.label} (${field.path})`);
      throw new Error(`Required system field is unresolved: ${field.label} (${field.path})`);
    }
  }
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

export async function executeCapability(
  cap: CapabilityContract,
  input: Record<string, unknown>,
  confirmWrite = false
) {
  if (cap.validation.status !== "verified") throw new Error(`Capability ${cap.id} is not verified`);
  if (cap.confirmation.required && !confirmWrite) {
    throw new Error(`Capability ${cap.id} requires explicit write confirmation`);
  }

  const prepared = prepareInput(cap, input);
  const { url, consumed } = expandUrl(cap, prepared);
  const method = cap.transport.method.toUpperCase();
  const headers: Record<string, string> = {
    accept: "application/json",
    ...authHeaders()
  };

  const init: RequestInit = { method, headers };
  if (!["GET", "HEAD"].includes(method)) {
    const body = structuredClone(prepared);
    for (const fieldPath of consumed) deleteByPath(body, fieldPath);
    headers["content-type"] = "application/json";
    init.body = JSON.stringify(body);
  } else {
    for (const field of cap.inputForm) {
      const value = getByPath(prepared, field.path);
      if (!consumed.has(field.path) && value !== undefined && typeof value !== "object") {
        url.searchParams.set(field.name, String(value));
      }
    }
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
