import type { CapabilityContract } from "../domain.js";
import { getByPath } from "../utils.js";

function expandUrl(template: string, input: Record<string, unknown>) {
  const url = new URL(template);
  const consumed = new Set<string>();

  url.pathname = url.pathname.replace(/\{([^}]+)\}/g, (_, key) => {
    const value = input[key];
    if (value === undefined) throw new Error(`Missing URL parameter: ${key}`);
    consumed.add(key);
    return encodeURIComponent(String(value));
  });

  for (const [key, value] of [...url.searchParams]) {
    const match = value.match(/^\{([^}]+)\}$/);
    if (!match) continue;
    const field = match[1]!;
    const actual = input[field];
    if (actual === undefined) {
      url.searchParams.delete(key);
      continue;
    }
    consumed.add(field);
    url.searchParams.set(key, String(actual));
  }

  return { url, consumed };
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

  const { url, consumed } = expandUrl(cap.transport.urlTemplate, input);
  const method = cap.transport.method.toUpperCase();
  const headers: Record<string, string> = {
    accept: "application/json",
    ...authHeaders()
  };

  const init: RequestInit = { method, headers };
  if (!["GET", "HEAD"].includes(method)) {
    const body = Object.fromEntries(Object.entries(input).filter(([key]) => !consumed.has(key)));
    headers["content-type"] = "application/json";
    init.body = JSON.stringify(body);
  } else {
    for (const [key, value] of Object.entries(input)) {
      if (!consumed.has(key) && value !== undefined) url.searchParams.set(key, String(value));
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
