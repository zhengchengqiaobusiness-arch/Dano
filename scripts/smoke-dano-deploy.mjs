#!/usr/bin/env node
const baseUrl = new URL(
  process.env.DANO_SMOKE_BASE_URL ||
    `http://127.0.0.1:${process.env.DANO_NGINX_PORT || "80"}`,
);

const timeoutMs = Number.parseInt(process.env.DANO_SMOKE_TIMEOUT_MS || "15000", 10);

function url(path) {
  return new URL(path, baseUrl).toString();
}

async function withTimeout(label, task) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await task(controller.signal);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`${label} failed: ${message}`);
  } finally {
    clearTimeout(timeout);
  }
}

async function expectResponse(label, path, init, predicate) {
  const response = await withTimeout(label, signal =>
    fetch(url(path), { ...init, signal }),
  );
  if (!predicate(response)) {
    throw new Error(`${label} returned ${response.status}`);
  }
  return response;
}

function parseSseMessages(buffer) {
  const messages = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  for (const part of parts) {
    const data = part
      .split(/\r?\n/)
      .filter(line => line.startsWith("data:"))
      .map(line => line.slice("data:".length).trimStart())
      .join("\n");
    if (!data) continue;
    try {
      messages.push(JSON.parse(data));
    } catch {
      messages.push({ type: "raw", payload: data });
    }
  }
  return { messages, rest };
}

async function waitForSseMessage(response, predicate) {
  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("SSE response did not expose a readable body");
  }

  const decoder = new TextDecoder();
  let buffer = "";
  const deadline = Date.now() + timeoutMs;

  try {
    while (Date.now() < deadline) {
      const remaining = deadline - Date.now();
      const timeout = new Promise((_, reject) => {
        setTimeout(() => reject(new Error("timed out waiting for SSE")), remaining);
      });
      const { value, done } = await Promise.race([reader.read(), timeout]);
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseMessages(buffer);
      buffer = parsed.rest;
      const match = parsed.messages.find(predicate);
      if (match) return match;
    }
  } finally {
    await reader.cancel().catch(() => {});
  }

  throw new Error("timed out waiting for matching SSE message");
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

console.log(`[smoke] base url: ${baseUrl.toString()}`);

const root = await expectResponse("GET /", "/", {}, response => response.ok);
const rootText = await root.text();
assert(/<html/i.test(rootText), "GET / did not return HTML");
console.log("[smoke] / loaded");

const health = await expectResponse(
  "GET /api/health",
  "/api/health",
  {},
  response => response.ok,
);
assert((await health.json()).status === "ok", "health status was not ok");
console.log("[smoke] /api/health ok");

const createdResponse = await expectResponse(
  "POST /api/clients",
  "/api/clients",
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  },
  response => response.status === 201,
);
const created = await createdResponse.json();
assert(created.client?.id, "client id missing");
assert(created.eventsUrl, "eventsUrl missing");
assert(created.messagesUrl, "messagesUrl missing");
console.log("[smoke] client created");

const sse = await expectResponse(
  "GET eventsUrl",
  created.eventsUrl,
  {},
  response =>
    response.ok &&
    response.headers.get("content-type")?.includes("text/event-stream"),
);
console.log("[smoke] SSE connected");

const commandId = `smoke-${Date.now()}`;
const posted = await expectResponse(
  "POST messagesUrl",
  created.messagesUrl,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: "command",
      payload: { id: commandId, type: "get_state" },
    }),
  },
  response => response.status === 202,
);
assert((await posted.json()).status === "accepted", "message was not accepted");
console.log("[smoke] message accepted");

const message = await waitForSseMessage(
  sse,
  event => event.type === "response" && event.payload?.id === commandId,
);
assert(
  typeof message.payload?.success === "boolean",
  "response did not include success state",
);
console.log(`[smoke] SSE response received: success=${message.payload.success}`);

await expectResponse(
  "POST disconnect",
  `/api/clients/${encodeURIComponent(created.client.id)}/disconnect`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  },
  response => response.status === 202,
);
console.log("[smoke] disconnect ok");
