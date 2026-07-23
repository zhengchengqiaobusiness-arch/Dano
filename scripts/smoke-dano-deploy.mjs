#!/usr/bin/env node
import { createHmac, timingSafeEqual } from "node:crypto";

const exposureMode = process.env.DANO_EXPOSURE_MODE?.trim() || "http";
const smokeUsesTls = exposureMode !== "http";
const defaultSmokeUrl = smokeUsesTls
  ? `https://127.0.0.1:${process.env.DANO_HTTPS_PORT || "443"}`
  : `http://127.0.0.1:${process.env.DANO_NGINX_PORT || "80"}`;
const baseUrl = new URL(process.env.DANO_SMOKE_BASE_URL || defaultSmokeUrl);

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
    const detail = (await response.clone().text().catch(() => ""))
      .replaceAll(/\s+/g, " ")
      .trim()
      .slice(0, 200);
    throw new Error(
      `${label} returned ${response.status}${detail ? `: ${detail}` : ""}`,
    );
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
      let timeoutId;
      const timeout = new Promise((_, reject) => {
        timeoutId = setTimeout(
          () => reject(new Error("timed out waiting for SSE")),
          remaining,
        );
      });
      const { value, done } = await Promise.race([reader.read(), timeout]).finally(
        () => clearTimeout(timeoutId),
      );
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

function demoCookieFrom(response) {
  const setCookie = response.headers.get("set-cookie");
  if (!setCookie) return null;

  const parts = setCookie.split(";").map(part => part.trim());
  const separator = parts[0]?.indexOf("=") ?? -1;
  assert(separator > 0, "Demo Set-Cookie header is malformed");
  const name = parts[0].slice(0, separator);
  const token = parts[0].slice(separator + 1);
  const attributes = new Map(
    parts.slice(1).map(part => {
      const attributeSeparator = part.indexOf("=");
      return attributeSeparator < 0
        ? [part.toLowerCase(), true]
        : [
            part.slice(0, attributeSeparator).toLowerCase(),
            part.slice(attributeSeparator + 1),
          ];
    }),
  );
  const expectedName = process.env.DANO_AUTH_COOKIE_NAME?.trim() || "dano_auth";
  assert(name === expectedName, `Demo cookie name was ${name}`);
  assert(token, "Demo cookie token is empty");
  assert(attributes.get("path") === "/", "Demo cookie Path was not /");
  assert(attributes.has("httponly"), "Demo cookie was not HttpOnly");
  assert(attributes.get("samesite") === "Lax", "Demo cookie SameSite was not Lax");
  assert(attributes.has("expires"), "Demo cookie was not persistent");
  assert(
    attributes.has("secure") === (baseUrl.protocol === "https:"),
    "Demo cookie Secure did not match the deployment exposure",
  );

  let claims;
  try {
    const payload = token.split(".")[1];
    claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
  } catch {
    throw new Error("Demo cookie JWT payload is invalid");
  }
  const expiresSeconds = Date.parse(String(attributes.get("expires"))) / 1000;
  assert(Number.isFinite(expiresSeconds), "Demo cookie Expires is invalid");
  assert(Number.isInteger(claims.exp), "Demo cookie JWT exp is invalid");
  assert(expiresSeconds <= claims.exp, "Demo cookie lifetime exceeds JWT exp");
  const secret = process.env.DANO_AUTH_JWT_SECRET?.trim();
  if (secret) {
    const jwtParts = token.split(".");
    const expected = createHmac("sha256", secret)
      .update(`${jwtParts[0]}.${jwtParts[1]}`)
      .digest();
    const actual = Buffer.from(jwtParts[2], "base64url");
    assert(
      actual.length === expected.length && timingSafeEqual(actual, expected),
      "Demo cookie JWT does not match the deployment Secret",
    );
  }

  return `${name}=${token}`;
}

function requestHeaders(cookie, values = {}) {
  return cookie ? { Cookie: cookie, ...values } : values;
}

console.log(`[smoke] base url: ${baseUrl.toString()}`);

const root = await expectResponse("GET /", "/", {}, response => response.ok);
const rootText = await root.text();
assert(/<html/i.test(rootText), "GET / did not return HTML");
const demoCookie = demoCookieFrom(root);
console.log("[smoke] / loaded");
if (demoCookie) console.log("[smoke] Demo authentication cookie verified");

const health = await expectResponse(
  "GET /api/health",
  "/api/health",
  { headers: requestHeaders(demoCookie) },
  response => response.ok,
);
assert((await health.json()).status === "ok", "health status was not ok");
console.log("[smoke] /api/health ok");

const createdResponse = await expectResponse(
  "POST /api/clients",
  "/api/clients",
  {
    method: "POST",
    headers: requestHeaders(demoCookie, { "Content-Type": "application/json" }),
    body: "{}",
  },
  response => response.status === 201,
);
const created = await createdResponse.json();
assert(created.client?.id, "client id missing");
assert(created.eventsUrl, "eventsUrl missing");
assert(created.messagesUrl, "messagesUrl missing");
if (demoCookie) {
  assert(
    created.currentUser?.username === "演示用户",
    "Demo cookie did not resolve to 演示用户",
  );
}
console.log("[smoke] client created");

const sse = await expectResponse(
  "GET eventsUrl",
  created.eventsUrl,
  { headers: requestHeaders(demoCookie) },
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
    headers: requestHeaders(demoCookie, { "Content-Type": "application/json" }),
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
    headers: requestHeaders(demoCookie, { "Content-Type": "application/json" }),
    body: "{}",
  },
  response => response.status === 202,
);
console.log("[smoke] disconnect ok");
