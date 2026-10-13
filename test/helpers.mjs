import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

export const PASSWORD = "correct-horse-battery";

export async function withData(run) {
  const root = await mkdtemp(path.join(os.tmpdir(), "cabp-data-"));
  const previous = process.env.CABP_DATA;
  process.env.CABP_DATA = root;
  try {
    return await run(root);
  } finally {
    if (previous === undefined) delete process.env.CABP_DATA;
    else process.env.CABP_DATA = previous;
  }
}

export async function jsonRequest(port, method, pathname, body, extraHeaders = {}) {
  const headers = { ...extraHeaders };
  const payload = body === undefined ? undefined : JSON.stringify(body);
  if (payload !== undefined) headers["content-type"] = "application/json";
  const res = await fetch(`http://127.0.0.1:${port}${pathname}`, { method, headers, body: payload });
  const text = await res.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }
  return { status: res.status, data };
}

export function authHeader(apiKey) {
  return { "X-Tenant-Key": apiKey };
}

export async function makeTenant(port, tenant, { username = tenant, password = PASSWORD } = {}) {
  const created = await jsonRequest(port, "POST", "/tenants", { tenant, username, password });
  assert.equal(created.status, 200, JSON.stringify(created.data));
  assert.match(String(created.data.api_key || ""), /^dk_/);
  return created.data;
}
