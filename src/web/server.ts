import { spawn } from "node:child_process";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import http, { type IncomingMessage, type ServerResponse } from "node:http";
import path from "node:path";
import { chromium } from "playwright";
import { loadConfig } from "../config.js";
import { StudioService } from "../studio-service.js";
import { PiRpcBridge } from "./pi-rpc.js";
import { PiTranscript } from "./transcript.js";

const host = "127.0.0.1";
const port = Number(process.env.BSS_PORT || 4310);
const origin = `http://${host}:${port}`;
const publicDir = path.resolve(process.cwd(), "web");
const browserServiceToken = randomBytes(32).toString("hex");
const studio = new StudioService({ ...loadConfig(), headless: true });
const pi = new PiRpcBridge(process.cwd(), origin, browserServiceToken);

type BrowserInteractionMode = "manual" | "automatic";
type RuntimeLogLevel = "PLAIN" | "CHECK" | "START" | "INFO" | "READY" | "BROWSER" | "PI" | "TOOL" | "WAIT" | "WARN" | "ERROR";
const eventClients = new Set<ServerResponse>();
const secretValues = Object.entries(process.env)
  .filter(([key, value]) => /KEY|TOKEN|SECRET|PASSWORD/i.test(key) && typeof value === "string" && value.length >= 6)
  .map(([, value]) => value as string)
  .sort((left, right) => right.length - left.length);
let browserInteractionMode: BrowserInteractionMode = "automatic";

const contentTypes: Record<string, string> = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml"
};

function sendJson(response: ServerResponse, status: number, payload: unknown) {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store"
  });
  response.end(JSON.stringify(payload));
}

function sendEvent(response: ServerResponse, payload: unknown) {
  response.write(`data: ${JSON.stringify(payload)}\n\n`);
}

function broadcast(payload: unknown) {
  for (const client of eventClients) sendEvent(client, payload);
}

function safeLogMessage(value: unknown) {
  let message = String(value ?? "");
  for (const secret of secretValues) message = message.split(secret).join("[REDACTED]");
  return message.slice(0, 8_000);
}

function runtimeLog(level: RuntimeLogLevel, message: unknown) {
  const safeMessage = safeLogMessage(message);
  const line = level === "PLAIN" ? safeMessage : `[${level}] ${safeMessage}`;
  if (level === "ERROR") console.error(line);
  else if (level === "WARN") console.warn(line);
  else console.log(line);
}

function sanitizeTranscript(value: unknown, key = "", depth = 0): unknown {
  if (/KEY|TOKEN|SECRET|PASSWORD|AUTHORIZATION|COOKIE/i.test(key)) return "[REDACTED]";
  if (depth > 8) return "[内容层级过深]";
  if (typeof value === "string") return safeLogMessage(value).slice(0, 30_000);
  if (Array.isArray(value)) return value.slice(0, 200).map(item => sanitizeTranscript(item, key, depth + 1));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .slice(0, 300).map(([childKey, child]) => [childKey, sanitizeTranscript(child, childKey, depth + 1)]));
  return value;
}

const transcript = new PiTranscript(sanitizeTranscript);

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

async function readJsonBody(request: IncomingMessage): Promise<any> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.byteLength;
    if (size > 1_048_576) throw new Error("Request body is too large");
    chunks.push(buffer);
  }
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function parseBrowserUrl(value: unknown) {
  if (typeof value !== "string" || !value.trim()) throw new Error("A browser URL is required");
  const url = new URL(value.trim());
  if (!new Set(["http:", "https:"]).has(url.protocol)) throw new Error("Only http and https browser URLs are supported");
  return url.toString();
}

function parseBrowserMode(value: unknown): BrowserInteractionMode {
  if (value === "manual" || value === "automatic") return value;
  throw new Error("录制模式必须是 manual 或 automatic");
}

function setBrowserMode(mode: BrowserInteractionMode) {
  if (browserInteractionMode === mode) return;
  browserInteractionMode = mode;
  runtimeLog("BROWSER", mode === "manual" ? "Switched to manual recording mode." : "Switched to Pi automatic click mode.");
  broadcast({ type: "browser_mode", mode });
}

function authorized(request: IncomingMessage) {
  const supplied = request.headers.authorization?.replace(/^Bearer\s+/i, "") || "";
  const expected = Buffer.from(browserServiceToken);
  const actual = Buffer.from(supplied);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

pi.subscribe(event => {
  for (const payload of transcript.handle(event)) broadcast(payload);
  if (event.type === "agent_ready") {
    broadcast({ type: "agent_status", ready: true, streaming: false });
    runtimeLog("READY", `Pi connected. Provider: ${process.env.PI_PROVIDER || "xiaomi-token-plan-cn"}; model: ${process.env.PI_MODEL || "provider default"}.`);
  }
  if (event.type === "agent_start") {
    runtimeLog("PI", "Natural-language task started.");
    broadcast({ type: "agent_status", ready: true, streaming: true });
  }
  if (event.type === "agent_settled") {
    broadcast({ type: "agent_status", ready: true, streaming: false });
    runtimeLog("PI", "Natural-language task completed.");
  }
  if (event.type === "extension_ui_request") {
    if (event.method === "confirm") {
      runtimeLog("PI", `Auto-approved confirmation: ${event.title || event.message || "operation"}.`);
      try {
        pi.respondToUi({ id: event.id, confirmed: true });
      } catch (error) {
        runtimeLog("WARN", `Failed to auto-approve confirmation: ${errorMessage(error)}`);
      }
      return;
    }
    runtimeLog("WAIT", `Pi requested ${event.method || "user input"}.`);
    const safeRequest = {
      type: "ui_request",
      id: event.id,
      method: event.method,
      title: event.title,
      message: event.message,
      options: event.options,
      placeholder: event.placeholder,
      prefill: event.prefill,
      notifyType: event.notifyType
    };
    broadcast(safeRequest);
  }
  if (event.type === "agent_diagnostic") {
    runtimeLog("WARN", event.message || "Pi reported a diagnostic message.");
    broadcast({ type: "agent_error", message: event.message || "Pi reported a diagnostic message" });
  }
  if (event.type === "agent_process_exit") {
    runtimeLog("ERROR", event.message || `Pi process stopped with code ${event.code ?? "unknown"}.`);
    broadcast({ type: "agent_error", message: event.message || "Pi stopped unexpectedly" });
  }
});

async function resetWorkbench() {
  if (pi.status().streaming) {
    await pi.abort().catch(() => {});
    broadcast({ type: "agent_status", ready: pi.status().ready, streaming: false });
  }
  if (pi.status().ready) {
    await pi.newSession().catch(error => {
      runtimeLog("WARN", `Failed to start a new Pi session: ${errorMessage(error)}`);
    });
  }
  transcript.clear();
  broadcast({ type: "session_reset" });
}

async function browserStart(url: string, name?: string) {
  return studio.startRecording(url, name || "web-session");
}

async function browserState() {
  return { ...(await studio.recorder.state()), mode: browserInteractionMode };
}

async function handleApi(request: IncomingMessage, response: ServerResponse, pathname: string) {
  if (request.method === "GET" && pathname === "/api/events") {
    response.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "Connection": "keep-alive"
    });
    response.write("retry: 1500\n\n");
    eventClients.add(response);
    sendEvent(response, { type: "connected" });
    request.on("close", () => eventClients.delete(response));
    return;
  }

  if (request.method === "GET" && pathname === "/api/status") {
    sendJson(response, 200, {
      agent: pi.status(),
      browser: await browserState(),
      model: process.env.PI_MODEL || null,
      provider: process.env.PI_PROVIDER || "xiaomi-token-plan-cn",
      thinking: process.env.PI_THINKING || "medium",
      sessionItems: transcript.items
    });
    return;
  }

  if (request.method === "GET" && pathname === "/api/browser/state") {
    sendJson(response, 200, await browserState());
    return;
  }

  if (request.method === "GET" && pathname === "/api/browser/frame") {
    if (!studio.recorder.isActive()) {
      response.writeHead(204, { "Cache-Control": "no-store" }).end();
      return;
    }
    const frame = await studio.recorder.preview();
    response.writeHead(200, {
      "Content-Type": "image/jpeg",
      "Content-Length": String(frame.byteLength),
      "Cache-Control": "no-store, max-age=0"
    });
    response.end(frame);
    return;
  }

  if (request.method === "GET" && pathname === "/api/skills") {
    sendJson(response, 200, { skills: await studio.listSkills() });
    return;
  }

  if (request.method === "POST" && pathname === "/api/skills/export") {
    const body = await readJsonBody(request);
    if (typeof body.name !== "string" || !body.name.trim()) throw new Error("请输入 Skill 名称");
    const record = await studio.exportManaged(body.name, body.confirmed === true);
    sendJson(response, 200, record);
    broadcast({ type: "skills_changed" });
    return;
  }

  const skillMatch = pathname.match(/^\/api\/skills\/([^/]+)\/(freeze|delete|invoke)$/);
  if (skillMatch) {
    const name = decodeURIComponent(skillMatch[1]!);
    const action = skillMatch[2];
    const body = await readJsonBody(request);
    if (request.method === "POST" && action === "freeze") {
      const record = await studio.setSkillFrozen(name, body.frozen === true, body.confirmed === true);
      sendJson(response, 200, record);
      broadcast({ type: "skills_changed" });
      return;
    }
    if (request.method === "DELETE" && action === "delete") {
      const record = await studio.deleteSkill(name, body.confirmed === true);
      sendJson(response, 200, record);
      broadcast({ type: "skills_changed" });
      return;
    }
    if (request.method === "POST" && action === "invoke") {
      const invocation = await studio.invokeSkill(name, typeof body.goal === "string" ? body.goal : "");
      const userEvent = transcript.addUser(invocation.prompt);
      broadcast(userEvent);
      await pi.prompt(invocation.prompt);
      sendJson(response, 202, { accepted: true, skill: invocation.record.name });
      return;
    }
  }

  if (request.method === "POST" && pathname === "/api/browser/open") {
    const body = await readJsonBody(request);
    if (body.mode !== undefined) setBrowserMode(parseBrowserMode(body.mode));
    const session = await browserStart(parseBrowserUrl(body.url), body.name);
    runtimeLog("BROWSER", `${browserInteractionMode === "manual" ? "Manual" : "Pi automatic"} recording session started.`);
    sendJson(response, 200, { session, state: await browserState() });
    broadcast({ type: "browser_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/browser/mode") {
    const body = await readJsonBody(request);
    setBrowserMode(parseBrowserMode(body.mode));
    sendJson(response, 200, await browserState());
    broadcast({ type: "browser_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/browser/manual") {
    const result = await studio.recorder.manualControl(await readJsonBody(request)) as { observed?: { eventType?: string; label?: string; name?: string; value?: unknown; selector?: string } };
    if (result.observed && (result.observed.eventType === "input" || result.observed.eventType === "change" || result.observed.label || result.observed.value !== undefined)) {
      const label = result.observed.label || result.observed.name || "页面字段";
      runtimeLog("BROWSER", `Manual ${result.observed.eventType || "action"}: ${label}=${String(result.observed.value ?? "")}`);
      if (result.observed.eventType === "input" || result.observed.eventType === "change") {
        broadcast(transcript.addManual(result.observed));
      }
    }
    sendJson(response, 200, result);
    return;
  }

  if (request.method === "POST" && pathname === "/api/browser/reload") {
    const result = await studio.recorder.reload();
    runtimeLog("BROWSER", "Embedded browser page reloaded.");
    sendJson(response, 200, result);
    broadcast({ type: "browser_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/browser/stop") {
    const session = await studio.stopRecording();
    runtimeLog("BROWSER", "Recording session stopped and evidence was saved.");
    sendJson(response, 200, session);
    broadcast({ type: "browser_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/session/clear") {
    await resetWorkbench();
    runtimeLog("PI", "Workbench conversation history was cleared.");
    sendJson(response, 200, { cleared: true, sessionItems: transcript.items });
    return;
  }

  if (request.method === "POST" && pathname === "/api/chat") {
    const body = await readJsonBody(request);
    const messageText = typeof body.message === "string" ? body.message.trim() : "";
    if (!messageText) throw new Error("A message is required");
    const userEvent = transcript.addUser(messageText);
    broadcast(userEvent);
    await pi.prompt(messageText);
    sendJson(response, 202, { accepted: true, item: userEvent.item });
    return;
  }

  if (request.method === "POST" && pathname === "/api/agent/abort") {
    await pi.abort();
    sendJson(response, 200, { aborted: true });
    return;
  }

  if (request.method === "POST" && pathname === "/api/agent/ui-response") {
    const body = await readJsonBody(request);
    pi.respondToUi(body);
    sendJson(response, 200, { accepted: true });
    return;
  }

  if (pathname.startsWith("/internal/browser/")) {
    if (!authorized(request)) {
      sendJson(response, 401, { error: "Unauthorized" });
      return;
    }
    const body = await readJsonBody(request);
    if (request.method === "POST" && pathname === "/internal/browser/start") {
      if (browserInteractionMode !== "automatic") throw new Error("当前是手动录制模式；请在前端切换到 Pi 自动点击后再让 Pi 启动浏览器");
      sendJson(response, 200, await browserStart(parseBrowserUrl(body.url), body.name));
      broadcast({ type: "browser_changed" });
      return;
    }
    if (request.method === "POST" && pathname === "/internal/browser/stop") {
      if (browserInteractionMode !== "automatic") throw new Error("当前是手动录制模式，Pi 不能停止手动会话");
      sendJson(response, 200, await studio.stopRecording());
      broadcast({ type: "browser_changed" });
      return;
    }
    if (request.method === "POST" && pathname === "/internal/browser/inspect") {
      if (typeof body.selector !== "string" || !body.selector) throw new Error("A selector is required");
      sendJson(response, 200, await studio.recorder.inspectTarget(body.selector));
      return;
    }
    if (request.method === "POST" && pathname === "/internal/browser/control") {
      if (browserInteractionMode !== "automatic" && new Set(["goto", "click", "fill", "select", "choose", "press"]).has(String(body.action))) {
        throw new Error("当前是手动录制模式；Pi 只能读取页面，不能自动点击或输入");
      }
      sendJson(response, 200, await studio.recorder.control(body));
      broadcast({ type: "browser_changed" });
      return;
    }
  }

  sendJson(response, 404, { error: "Not found" });
}

async function serveStatic(response: ServerResponse, pathname: string) {
  const relativePath = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  const filePath = path.resolve(publicDir, relativePath);
  if (!filePath.startsWith(publicDir + path.sep)) {
    response.writeHead(403).end("Forbidden");
    return;
  }
  try {
    const file = await stat(filePath);
    if (!file.isFile()) throw new Error("Not a file");
    response.writeHead(200, {
      "Content-Type": contentTypes[path.extname(filePath)] || "application/octet-stream",
      "Cache-Control": "no-store"
    });
    createReadStream(filePath).pipe(response);
  } catch {
    sendJson(response, 404, { error: "Not found" });
  }
}

const server = http.createServer(async (request, response) => {
  const pathname = new URL(request.url || "/", origin).pathname;
  try {
    if (pathname.startsWith("/api/") || pathname.startsWith("/internal/")) {
      await handleApi(request, response, pathname);
    } else {
      await serveStatic(response, pathname);
    }
  } catch (error) {
    sendJson(response, 400, { error: errorMessage(error) });
  }
});

const heartbeat = setInterval(() => {
  for (const client of eventClients) client.write(": heartbeat\n\n");
}, 15_000);

let shuttingDown = false;

function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  clearInterval(heartbeat);
  try { broadcast({ type: "studio_shutdown" }); } catch { /* clients may already be gone */ }
  pi.stop();
  studio.recorder.disposeImmediate();
  try { server.close(); } catch { /* already closed */ }
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
process.on("SIGHUP", shutdown);
process.on("SIGBREAK", shutdown);

async function packageVersion(relativePath: string) {
  try {
    const parsed = JSON.parse(await readFile(path.resolve(process.cwd(), relativePath), "utf8"));
    return typeof parsed.version === "string" ? parsed.version : null;
  } catch {
    return null;
  }
}

async function logStartupEnvironment() {
  runtimeLog("PLAIN", "========================================");
  runtimeLog("PLAIN", "Pi Business Skill Studio");
  runtimeLog("PLAIN", "========================================");
  runtimeLog("CHECK", existsSync(path.resolve(process.cwd(), ".env")) ? ".env configuration detected." : ".env is not present; process environment and defaults will be used.");
  const chromiumPath = chromium.executablePath();
  runtimeLog(existsSync(chromiumPath) ? "CHECK" : "WARN", existsSync(chromiumPath) ? "Playwright Chromium is available for the embedded browser." : "Playwright Chromium executable was not found.");
  const piVersion = await packageVersion("node_modules/@earendil-works/pi-coding-agent/package.json");
  runtimeLog("INFO", `Pi${piVersion ? ` v${piVersion}` : ""}; provider ${process.env.PI_PROVIDER || "xiaomi-token-plan-cn"}; model ${process.env.PI_MODEL || "provider default"}.`);
  runtimeLog("START", "Launching Pi Business Skill Studio...");
}

server.on("error", (error: NodeJS.ErrnoException) => {
  clearInterval(heartbeat);
  if (error.code === "EADDRINUSE") runtimeLog("ERROR", `Port ${port} is still in use after leftover cleanup. Close the occupying process and start again.`);
  else runtimeLog("ERROR", `Studio server failed: ${errorMessage(error)}`);
  process.exit(1);
});

await logStartupEnvironment();

server.listen(port, host, async () => {
  runtimeLog("READY", `Pi Business Skill Studio is ready at ${origin}`);
  if (["1", "true", "yes"].includes(String(process.env.BSS_OPEN_UI || "").toLowerCase()) && process.platform === "win32") {
    spawn("cmd.exe", ["/d", "/c", "start", "", origin], {
      stdio: "ignore",
      windowsHide: true
    });
  }
  try {
    await pi.start();
  } catch (error) {
    runtimeLog("ERROR", `Pi failed to start: ${errorMessage(error)}`);
  }
});
