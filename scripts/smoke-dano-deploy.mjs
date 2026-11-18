#!/usr/bin/env node
const exposureMode = process.env.DANO_EXPOSURE_MODE?.trim() || "http";
const smokeUsesTls = exposureMode !== "http";
const defaultSmokeUrl = smokeUsesTls
  ? `https://127.0.0.1:${process.env.DANO_HTTPS_PORT || "443"}`
  : `http://127.0.0.1:${process.env.DANO_NGINX_PORT || "80"}`;
const baseUrl = new URL(process.env.DANO_SMOKE_BASE_URL || defaultSmokeUrl);

const timeoutMs = Number.parseInt(process.env.DANO_SMOKE_TIMEOUT_MS || "15000", 10);

function url(path) {
  const target = new URL(path, baseUrl);
  assert(target.origin === baseUrl.origin, "smoke request must use the deployment origin");
  return target.toString();
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
    fetch(url(path), { ...init, redirect: "error", signal }),
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

function guestCookieFrom(response) {
  const setCookie = response.headers.get("set-cookie");
  assert(setCookie, "Anonymous client did not receive a guest Cookie");

  const parts = setCookie.split(";").map(part => part.trim());
  const separator = parts[0]?.indexOf("=") ?? -1;
  assert(separator > 0, "Anonymous Set-Cookie header is malformed");
  const name = parts[0].slice(0, separator);
  const guestId = parts[0].slice(separator + 1);
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
  assert(name === "dano_guest", `Anonymous cookie name was ${name}`);
  assert(/^[A-Za-z0-9_-]{43}$/.test(guestId), "Anonymous guest ID is not opaque");
  assert(attributes.get("path") === "/", "Anonymous cookie Path was not /");
  assert(attributes.has("httponly"), "Anonymous cookie was not HttpOnly");
  assert(
    attributes.get("samesite") === "Lax",
    "Anonymous cookie SameSite was not Lax",
  );
  assert(!attributes.has("domain"), "Anonymous cookie must remain host-only");
  assert(
    attributes.has("secure") === (baseUrl.protocol === "https:"),
    "Anonymous cookie Secure did not match the deployment exposure",
  );
  return `${name}=${guestId}`;
}

function requestHeaders(cookie, values = {}) {
  return cookie ? { Cookie: cookie, ...values } : values;
}

async function checkAssets(html) {
  const assets = [...new Set([...html.matchAll(/\b(?:src|href)\s*=\s*["']([^"']+)["']/gi)]
    .map(match => new URL(match[1], baseUrl))
    .filter(asset => /\.(?:js|css)$/.test(asset.pathname))
    .map(asset => {
      assert(asset.origin === baseUrl.origin, "entry asset must use the deployment origin");
      return asset.pathname;
    }))].sort();
  assert(assets.some(path => path.endsWith(".js")), "homepage has no JavaScript entry");
  assert(assets.some(path => path.endsWith(".css")), "homepage has no stylesheet");
  const expectedJson = process.env.DANO_SMOKE_EXPECTED_ASSETS_JSON;
  if (expectedJson !== undefined) {
    const expected = JSON.parse(expectedJson);
    assert(Array.isArray(expected) && expected.length > 0 &&
      expected.every(name => typeof name === "string" && /^[^/]+\.(?:js|css)$/.test(name)),
    "candidate asset manifest is invalid");
    assert(JSON.stringify(assets) === JSON.stringify([...new Set(expected.map(name => `/assets/${name}`))].sort()),
      "homepage assets differ from the expected image manifest");
    console.log("[smoke] homepage assets match image manifest");
  } else {
    console.log("[smoke] image manifest not supplied; checking asset availability only");
  }
  for (const path of assets) {
    await withTimeout("GET entry asset", async signal => {
      const response = await fetch(url(path), { cache: "no-store", redirect: "error", signal });
      assert(response.ok, "entry asset request failed");
      const contentType = response.headers.get("content-type")?.split(";")[0].trim().toLowerCase();
      assert(path.endsWith(".css") ? contentType === "text/css" :
        /^(?:text|application)\/(?:javascript|ecmascript)$/.test(contentType ?? ""),
      "entry asset has an incorrect Content-Type");
      assert((await response.arrayBuffer()).byteLength > 0, "entry asset is empty");
    });
  }
  console.log("[smoke] JavaScript and CSS assets loaded");
}

console.log(`[smoke] base url: ${baseUrl.toString()}`);

const root = await expectResponse("GET /", "/", {}, response => response.ok);
const rootText = await root.text();
assert(/<html/i.test(rootText), "GET / did not return HTML");
console.log("[smoke] / loaded");
await checkAssets(rootText);

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
const guestCookie = guestCookieFrom(createdResponse);
const created = await createdResponse.json();
assert(created.client?.id, "client id missing");
assert(created.eventsUrl, "eventsUrl missing");
assert(created.messagesUrl, "messagesUrl missing");
assert(created.authentication?.status === "anonymous", "client was not Anonymous");
assert(!created.currentUser, "Anonymous client exposed a current User");
console.log("[smoke] Anonymous User cookie verified");
console.log("[smoke] client created");

let sse;
try {
  sse = await expectResponse(
    "GET eventsUrl",
    created.eventsUrl,
    { headers: requestHeaders(guestCookie) },
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
      headers: requestHeaders(guestCookie, { "Content-Type": "application/json" }),
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
    message.payload?.type === "response" && message.payload.command === "get_state" &&
      message.payload.success === true,
    "get_state did not return a successful response",
  );
  const state = message.payload.data;
  assert(state && typeof state === "object" && !Array.isArray(state) &&
    typeof state.sessionId === "string" && state.sessionId.length > 0 &&
    Number.isSafeInteger(state.messageCount) && state.messageCount >= 0 &&
    typeof state.isStreaming === "boolean",
  "get_state returned an invalid session state");
  console.log(`[smoke] SSE response received: success=${message.payload.success}`);
} finally {
  await sse?.body?.cancel().catch(() => {});
  await expectResponse(
    "POST disconnect",
    `/api/clients/${encodeURIComponent(created.client.id)}/disconnect`,
    {
      method: "POST",
      headers: requestHeaders(guestCookie, { "Content-Type": "application/json" }),
      body: "{}",
    },
    response => response.status === 202,
  );
  console.log("[smoke] disconnect ok");
}
