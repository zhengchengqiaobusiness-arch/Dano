import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { createServer as createHttpServer } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer as createNetServer } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import { chromium } from "playwright-core";

const repoRoot = resolve(import.meta.dirname, "..");
let backendOrigin;
let frontendOrigin;
const chromeCandidates = [
  process.env.DANO_CHROME_EXECUTABLE,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);

const runtimeDir = mkdtempSync(join(tmpdir(), "dano-multi-tab-browser-"));
const agentConfigDir = join(runtimeDir, "empty-pi-agent");
mkdirSync(agentConfigDir, { recursive: true });

const serviceLogs = [];
const services = [];
let browser;
let fakeProviderServer;

function logStep(message) {
  console.log(`[multi-tab-browser] ${message}`);
}

function sanitizedEnvironment(overrides = {}) {
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (
      /(?:_API_KEY|_ACCESS_KEY|_AUTH_TOKEN|_ACCESS_TOKEN|_SECRET_KEY)$/.test(
        key,
      )
    ) {
      delete env[key];
    }
  }
  delete env.PORT;
  return {
    ...env,
    DANO_RUNTIME_DIR: runtimeDir,
    PI_CODING_AGENT_DIR: agentConfigDir,
    ...overrides,
  };
}

function startService(name, args, envOverrides = {}) {
  const child = spawn("pnpm", args, {
    cwd: repoRoot,
    env: sanitizedEnvironment(envOverrides),
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  services.push({ name, child });
  for (const [streamName, stream] of [
    ["stdout", child.stdout],
    ["stderr", child.stderr],
  ]) {
    stream.setEncoding("utf8");
    stream.on("data", chunk => {
      for (const line of chunk.split(/\r?\n/)) {
        if (line) serviceLogs.push(`[${name}:${streamName}] ${line}`);
      }
    });
  }
  child.once("error", error => {
    serviceLogs.push(`[${name}:spawn] ${error.stack ?? error.message}`);
  });
  return child;
}

async function availablePort() {
  const server = createNetServer();
  await new Promise((resolveListen, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveListen);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const port = address.port;
  await new Promise((resolveClose, reject) =>
    server.close(error => (error ? reject(error) : resolveClose())),
  );
  return port;
}

async function startFakeProvider() {
  assert.deepEqual(
    readdirSync(agentConfigDir),
    [],
    "isolated PI_CODING_AGENT_DIR must start empty",
  );
  fakeProviderServer = createHttpServer((request, response) => {
    if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
      response.writeHead(404).end();
      return;
    }
    request.resume();
    response.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    const base = {
      id: "dano-local-browser-test",
      object: "chat.completion.chunk",
      created: Math.floor(Date.now() / 1000),
      model: "mimo-v2.5",
    };
    response.write(
      `data: ${JSON.stringify({ ...base, choices: [{ index: 0, delta: { role: "assistant", content: "" }, finish_reason: null }] })}\n\n`,
    );
    response.write(
      `data: ${JSON.stringify({ ...base, choices: [{ index: 0, delta: { content: "local test completion" }, finish_reason: null }] })}\n\n`,
    );
    response.write(
      `data: ${JSON.stringify({ ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\n`,
    );
    response.end("data: [DONE]\n\n");
  });
  await new Promise((resolveListen, reject) => {
    fakeProviderServer.once("error", reject);
    fakeProviderServer.listen(0, "127.0.0.1", resolveListen);
  });
  const address = fakeProviderServer.address();
  assert.ok(address && typeof address === "object");
  writeFileSync(
    join(agentConfigDir, "models.json"),
    `${JSON.stringify(
      {
        providers: {
          "xiaomi-token-plan-cn": {
            baseUrl: `http://127.0.0.1:${address.port}/v1`,
            api: "openai-completions",
            apiKey: "dano-local-browser-test-key",
            models: [
              {
                id: "mimo-v2.5",
                name: "Dano local browser test model",
                reasoning: false,
                input: ["text"],
                contextWindow: 128000,
                maxTokens: 4096,
              },
            ],
          },
        },
      },
      null,
      2,
    )}\n`,
    { mode: 0o600 },
  );
}

async function stopFakeProvider() {
  if (!fakeProviderServer) return;
  await new Promise(resolveClose => fakeProviderServer.close(resolveClose));
}

async function stopService({ child }) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise(resolveExit => child.once("exit", resolveExit));
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  if (await Promise.race([exited.then(() => true), delay(3_000, false)])) {
    return;
  }
  try {
    process.kill(-child.pid, "SIGKILL");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  await exited;
}

async function waitForHttp(url, label, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
      lastError = new Error(`${label} returned HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    const serviceExit = services.find(({ child }) => child.exitCode !== null);
    if (serviceExit) {
      throw new Error(`${serviceExit.name} exited with ${serviceExit.child.exitCode}`);
    }
    await delay(100);
  }
  throw new Error(`${label} did not become ready: ${lastError?.message ?? "timeout"}`);
}

async function waitFor(predicate, message, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const value = await predicate();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await delay(100);
  }
  throw new Error(`${message}${lastError ? `: ${lastError.message}` : ""}`);
}

function jsonlFiles(root) {
  const result = [];
  const visit = directory => {
    for (const entry of readdirSync(directory)) {
      const path = join(directory, entry);
      const stat = statSync(path);
      if (stat.isDirectory()) visit(path);
      else if (entry.endsWith(".jsonl")) result.push(path);
    }
  };
  visit(root);
  return result;
}

function markerJsonlUserRecordCount(marker) {
  let count = 0;
  for (const file of jsonlFiles(runtimeDir)) {
    for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
      if (!line.includes(marker)) continue;
      try {
        const record = JSON.parse(line);
        if (
          record.type === "message" &&
          record.message?.role === "user" &&
          JSON.stringify(record.message.content).includes(marker)
        ) {
          count += 1;
        }
      } catch {
        // A partially written final line is retried on the next poll.
      }
    }
  }
  return count;
}

async function openConnectedPage(context, ordinal) {
  const page = await context.newPage();
  let resolveClientId;
  const clientIdPromise = new Promise(resolveId => {
    resolveClientId = resolveId;
  });
  page.on("response", async response => {
    const request = response.request();
    if (
      request.method() === "POST" &&
      new URL(response.url()).pathname === "/api/clients" &&
      response.status() === 201
    ) {
      const body = await response.json();
      resolveClientId(body.client.id);
    }
  });
  await page.goto(frontendOrigin, { waitUntil: "domcontentloaded" });
  const textarea = page.locator("textarea.prompt-input");
  await textarea.waitFor({ state: "visible" });
  await waitFor(
    () => textarea.isEnabled(),
    `tab ${ordinal} did not connect to the Dano Bridge`,
    10_000,
  );
  const clientId = await Promise.race([
    clientIdPromise,
    delay(10_000).then(() => {
      throw new Error(`tab ${ordinal} did not expose its Bridge client id`);
    }),
  ]);
  return { page, clientId };
}

function projectedUserMessage(page, marker) {
  return page.locator("[data-user-message-index]", { hasText: marker });
}

async function sendAndWaitForAcceptance(page, marker, mode) {
  const textarea = page.locator("textarea.prompt-input");
  await textarea.fill(marker);
  const accepted = page.waitForResponse(response => {
    const request = response.request();
    return (
      request.method() === "POST" &&
      new URL(response.url()).pathname.endsWith("/messages") &&
      request.postData()?.includes(marker) === true
    );
  });
  if (mode === "enter") await textarea.press("Enter");
  else await page.locator("button.send-btn").click();
  const response = await accepted;
  assert.equal(response.status(), 202, `${marker} was not Bridge-accepted`);
  await waitFor(() => textarea.inputValue().then(value => value === ""), `${marker} draft did not clear`);
  await waitFor(
    () => markerJsonlUserRecordCount(marker) === 1,
    `${marker} did not produce exactly one user JSONL record`,
    10_000,
  );
  await projectedUserMessage(page, marker).waitFor({ state: "visible" });
  assert.equal(await projectedUserMessage(page, marker).count(), 1);
}

async function oldClientMessagesStatus(clientId) {
  return fetch(`${backendOrigin}/api/clients/${encodeURIComponent(clientId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: "command",
      payload: { id: "closed-client-probe", type: "get_state" },
    }),
  }).then(response => response.status);
}

async function run() {
  const chromeExecutable = chromeCandidates.find(candidate => existsSync(candidate));
  assert.ok(
    chromeExecutable,
    "No system Chrome/Chromium found. Set DANO_CHROME_EXECUTABLE to its executable path.",
  );

  const backendPort = await availablePort();
  const frontendPort = await availablePort();
  backendOrigin = `http://localhost:${backendPort}`;
  frontendOrigin = `http://localhost:${frontendPort}`;

  logStep(`using system Chromium: ${chromeExecutable}`);
  await startFakeProvider();
  logStep("using an isolated local fake provider; no real model credentials or requests");
  startService("backend", ["run", "dev:server"], {
    DANO_HOST: "127.0.0.1",
    DANO_PORT: String(backendPort),
  });
  await waitForHttp(`${backendOrigin}/api/health`, "Dano backend");
  startService(
    "vite",
    [
      "-C",
      "apps/dano",
      "exec",
      "vite",
      "--port",
      String(frontendPort),
      "--strictPort",
    ],
    { DANO_DEV_BACKEND_ORIGIN: backendOrigin },
  );
  await waitForHttp(frontendOrigin, "Vite frontend");

  browser = await chromium.launch({
    executablePath: chromeExecutable,
    headless: true,
    args: ["--disable-background-timer-throttling", "--disable-renderer-backgrounding"],
  });
  const context = await browser.newContext({ locale: "en-US" });
  const tabs = [];

  logStep("opening five same-origin tabs");
  for (let ordinal = 1; ordinal <= 5; ordinal += 1) {
    tabs.push(await openConnectedPage(context, ordinal));
  }

  const fiveTabMarker = `dano-five-tab-${Date.now()}`;
  await sendAndWaitForAcceptance(tabs[4].page, fiveTabMarker, "enter");
  logStep("five-tab prompt received 202, cleared, and projected exactly once");

  const constrainedPage = tabs[3].page;
  const attachmentName = `six-tab-attachment-${Date.now()}.txt`;
  const fileInput = constrainedPage.locator("input.hidden-file-input");
  await fileInput.setInputFiles({
    name: attachmentName,
    mimeType: "text/plain",
    buffer: Buffer.from("Dano multi-tab attachment regression fixture\n"),
  });
  const attachmentChip = constrainedPage.locator(".attachment-chip", {
    hasText: attachmentName,
  });
  await attachmentChip.waitFor({ state: "visible" });
  await waitFor(
    async () =>
      !(await attachmentChip.evaluate(element =>
        element.classList.contains("uploading") || element.classList.contains("failed"),
      )),
    "attachment did not finish uploading before the sixth tab opened",
  );

  const sixth = await openConnectedPage(context, 6);
  tabs.push(sixth);
  const constrainedMarker = `dano-six-tab-${Date.now()}`;
  const textarea = constrainedPage.locator("textarea.prompt-input");
  const sendButton = constrainedPage.locator("button.send-btn");
  let markerRequests = 0;
  let abortedMarkerRequests = 0;
  const markerRequestBodies = [];
  constrainedPage.on("request", request => {
    if (request.postData()?.includes(constrainedMarker)) {
      markerRequests += 1;
      markerRequestBodies.push(JSON.parse(request.postData()));
    }
  });
  constrainedPage.on("requestfailed", request => {
    if (request.postData()?.includes(constrainedMarker)) abortedMarkerRequests += 1;
  });

  await textarea.fill(constrainedMarker);
  await sendButton.click();
  await delay(300);
  assert.equal(await textarea.inputValue(), constrainedMarker, "pending draft was cleared before acknowledgement");
  assert.equal(await sendButton.isDisabled(), true, "pending submit did not disable duplicate submission");
  await constrainedPage.evaluate(() => {
    document.querySelector("button.send-btn")?.click();
  });
  assert.equal(markerRequests, 1, "duplicate submission created a second prompt request");
  const initialFiles = markerRequestBodies[0]?.payload?.files;
  assert.equal(initialFiles?.length, 1, "timed-out prompt did not carry the uploaded attachment");
  assert.equal(initialFiles[0]?.name, attachmentName);

  const timeoutToast = constrainedPage.locator(".toast-item.error", {
    hasText: /发送超时|Send timed out/,
  });
  await timeoutToast.waitFor({ state: "visible", timeout: 13_000 });
  assert.equal(await textarea.inputValue(), constrainedMarker, "timeout did not preserve the exact draft");
  assert.equal(await textarea.isEnabled(), true, "timeout did not restore an editable draft");
  assert.equal(await sendButton.isEnabled(), true, "timeout did not make explicit retry available");
  assert.equal(await attachmentChip.count(), 1, "timeout did not preserve the uploaded attachment");
  const removeAttachmentButton = attachmentChip.locator("button.attachment-chip-remove");
  assert.equal(await removeAttachmentButton.isEnabled(), true, "preserved attachment was not removable after timeout");
  assert.equal(markerJsonlUserRecordCount(constrainedMarker), 0, "unaccepted prompt reached session JSONL");
  await waitFor(() => abortedMarkerRequests === 1, "timed-out prompt request was not aborted");
  logStep("six-tab prompt stayed pending, rejected duplicates, timed out, and remained retryable");

  const closing = tabs[0];
  const pagehideBeacon = await closing.page.evaluate(() => {
    const result = { called: false, accepted: false, url: "" };
    const original = navigator.sendBeacon?.bind(navigator);
    if (!original) {
      window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: false }));
      return { ...result, diagnostic: "navigator.sendBeacon unavailable" };
    }
    navigator.sendBeacon = (url, data) => {
      const accepted = original(url, data);
      result.called = true;
      result.accepted = accepted;
      result.url = String(url);
      return accepted;
    };
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: false }));
    return result;
  });
  assert.equal(pagehideBeacon.called, true, "non-persisted pagehide did not invoke the Bridge disconnect beacon");
  await closing.page.close({ runBeforeUnload: true });

  if (pagehideBeacon.accepted) {
    await waitFor(
      async () => (await oldClientMessagesStatus(closing.clientId)) === 404,
      `pagehide beacon was accepted but old client ${closing.clientId} remained routable`,
    );
    logStep(`pagehide released Bridge client ${closing.clientId}`);
  } else {
    const status = await oldClientMessagesStatus(closing.clientId);
    console.warn(
      `[multi-tab-browser] DIAGNOSTIC: Chromium returned false while queueing the best-effort pagehide beacon; old client endpoint returned HTTP ${status}`,
    );
  }

  await delay(500);
  assert.equal(markerJsonlUserRecordCount(constrainedMarker), 0, "aborted prompt arrived after a connection slot was released");
  await sendAndWaitForAcceptance(constrainedPage, constrainedMarker, "button");
  assert.equal(markerRequests, 2, "explicit retry did not create exactly one new request");
  const retryFiles = markerRequestBodies[1]?.payload?.files;
  assert.deepEqual(
    retryFiles,
    initialFiles,
    "explicit retry did not carry the same preserved attachment",
  );
  assert.equal(markerJsonlUserRecordCount(constrainedMarker), 1, "explicit retry was not persisted exactly once");
  assert.equal(await projectedUserMessage(constrainedPage, constrainedMarker).count(), 1);
  logStep("aborted request did not arrive late; explicit retry preserved its attachment and projected/persisted exactly once");
}

try {
  await run();
  logStep("PASS");
} catch (error) {
  console.error(error?.stack ?? error);
  if (serviceLogs.length > 0) {
    console.error("\nLast service output:");
    console.error(serviceLogs.slice(-80).join("\n"));
  }
  process.exitCode = 1;
} finally {
  await browser?.close().catch(() => {});
  await Promise.allSettled(services.reverse().map(stopService));
  await stopFakeProvider().catch(() => {});
  rmSync(runtimeDir, { recursive: true, force: true });
}
