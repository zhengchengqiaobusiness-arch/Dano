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

const host = "127.0.0.1";
const port = Number(process.env.BSS_PORT || 4310);
const origin = `http://${host}:${port}`;
const publicDir = path.resolve(process.cwd(), "web");
const browserServiceToken = randomBytes(32).toString("hex");
const studio = new StudioService({ ...loadConfig(), headless: true });
const pi = new PiRpcBridge(process.cwd(), origin, browserServiceToken);

type ChatMessage = { id: string; role: "user" | "assistant"; text: string; at: string };
type BrowserInteractionMode = "manual" | "automatic";
type RuntimeLogLevel = "PLAIN" | "CHECK" | "START" | "INFO" | "READY" | "BROWSER" | "PI" | "TOOL" | "WAIT" | "WARN" | "ERROR";
type RuntimeLogEntry = { id: number; at: string; level: RuntimeLogLevel; line: string };
const chatMessages: ChatMessage[] = [];
const runtimeLogs: RuntimeLogEntry[] = [];
const eventClients = new Set<ServerResponse>();
const secretValues = Object.entries(process.env)
  .filter(([key, value]) => /KEY|TOKEN|SECRET|PASSWORD/i.test(key) && typeof value === "string" && value.length >= 6)
  .map(([, value]) => value as string)
  .sort((left, right) => right.length - left.length);
let assistantBuffer = "";
let runtimeLogId = 0;
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
  const entry: RuntimeLogEntry = { id: ++runtimeLogId, at: new Date().toISOString(), level, line };
  runtimeLogs.push(entry);
  if (runtimeLogs.length > 500) runtimeLogs.shift();
  if (level === "ERROR") console.error(line);
  else if (level === "WARN") console.warn(line);
  else console.log(line);
  broadcast({ type: "runtime_log", entry });
  return entry;
}

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

function messageText(message: any): string {
  if (typeof message?.content === "string") return message.content;
  if (!Array.isArray(message?.content)) return "";
  return message.content
    .filter((block: any) => block?.type === "text" && typeof block.text === "string")
    .map((block: any) => block.text)
    .join("\n");
}

pi.subscribe(event => {
  if (event.type === "agent_ready") {
    broadcast({ type: "agent_status", ready: true, streaming: false });
    runtimeLog("READY", `Pi connected. Provider: ${process.env.PI_PROVIDER || "xiaomi-token-plan-cn"}; model: ${process.env.PI_MODEL || "provider default"}.`);
  }
  if (event.type === "agent_start") {
    assistantBuffer = "";
    runtimeLog("PI", "Natural-language task started.");
    broadcast({ type: "agent_status", ready: true, streaming: true });
    broadcast({ type: "assistant_start" });
  }
  if (event.type === "message_update" && event.assistantMessageEvent?.type === "text_delta") {
    const delta = String(event.assistantMessageEvent.delta || "");
    assistantBuffer += delta;
    broadcast({ type: "assistant_delta", delta });
  }
  if (event.type === "message_end" && event.message?.role === "assistant") {
    const text = messageText(event.message) || assistantBuffer;
    if (text) {
      const message: ChatMessage = { id: `assistant-${Date.now()}`, role: "assistant", text, at: new Date().toISOString() };
      chatMessages.push(message);
      broadcast({ type: "assistant_done", message });
    }
    assistantBuffer = "";
  }
  if (event.type === "agent_settled") {
    broadcast({ type: "agent_status", ready: true, streaming: false });
    runtimeLog("PI", "Natural-language task completed.");
  }
  if (event.type === "tool_execution_start") {
    runtimeLog("TOOL", `${event.toolName || "unknown tool"} started.`);
    broadcast({ type: "tool_status", phase: "start", toolName: event.toolName, toolCallId: event.toolCallId });
  }
  if (event.type === "tool_execution_end") {
    runtimeLog(event.isError ? "ERROR" : "TOOL", `${event.toolName || "unknown tool"} ${event.isError ? "failed" : "completed"}.`);
    broadcast({
      type: "tool_status",
      phase: "end",
      toolName: event.toolName,
      toolCallId: event.toolCallId,
      isError: Boolean(event.isError)
    });
  }
  if (event.type === "extension_ui_request") {
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

async function browserStart(url: string, name?: string) {
  if (studio.recorder.isActive()) {
    await studio.recorder.control({ action: "goto", url });
    return studio.recorder.activeSession();
  }
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
      messages: chatMessages,
      logs: runtimeLogs
    });
    return;
  }

  if (request.method === "GET" && pathname === "/api/logs") {
    sendJson(response, 200, { logs: runtimeLogs });
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
      "Content-Type": "image/png",
      "Content-Length": String(frame.byteLength),
      "Cache-Control": "no-store, max-age=0"
    });
    response.end(frame);
    return;
  }

  if (request.method === "GET" && pathname === "/api/catalog") {
    const capabilities = await studio.capabilities();
    const routes = await studio.routes();
    sendJson(response, 200, {
      capabilities,
      routes,
      summary: {
        total: capabilities.length,
        verified: capabilities.filter(item => item.validation.status === "verified").length,
        candidates: capabilities.filter(item => item.validation.status === "candidate").length,
        fields: capabilities.reduce((count, item) => count + item.inputForm.length, 0),
        approvedBindings: capabilities.reduce((count, item) => count + item.bindings.filter(binding => binding.approved).length, 0)
      }
    });
    return;
  }

  if (request.method === "POST" && pathname === "/api/catalog/analyze") {
    const body = await readJsonBody(request);
    const capabilities = await studio.analyze(typeof body.sessionId === "string" ? body.sessionId : undefined, body.useLlm !== false);
    sendJson(response, 200, { capabilities });
    broadcast({ type: "catalog_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/catalog/validate") {
    const capabilities = await studio.validate();
    sendJson(response, 200, { capabilities });
    broadcast({ type: "catalog_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/bindings/approve") {
    const body = await readJsonBody(request);
    if (body.confirmed !== true) throw new Error("确认数据绑定前必须取得明确确认");
    const capability = await studio.approveBinding(body);
    sendJson(response, 200, capability);
    broadcast({ type: "catalog_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/candidates/configure") {
    const body = await readJsonBody(request);
    if (body.confirmed !== true) throw new Error("配置动态候选前必须取得明确确认");
    const capability = await studio.setDynamicCandidates(body);
    sendJson(response, 200, capability);
    broadcast({ type: "catalog_changed" });
    return;
  }

  const capabilityMatch = pathname.match(/^\/api\/capabilities\/([^/]+)$/);
  if (request.method === "PATCH" && capabilityMatch) {
    const capability = await studio.updateCapability(decodeURIComponent(capabilityMatch[1]!), await readJsonBody(request));
    sendJson(response, 200, capability);
    broadcast({ type: "catalog_changed" });
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
      const message: ChatMessage = { id: `user-${Date.now()}`, role: "user", text: invocation.prompt, at: new Date().toISOString() };
      chatMessages.push(message);
      broadcast({ type: "user_message", message });
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
    if (browserInteractionMode !== "manual") throw new Error("请先切换到手动录制模式");
    const result = await studio.recorder.manualControl(await readJsonBody(request));
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

  if (request.method === "POST" && pathname === "/api/chat") {
    const body = await readJsonBody(request);
    const messageText = typeof body.message === "string" ? body.message.trim() : "";
    if (!messageText) throw new Error("A message is required");
    const message: ChatMessage = { id: `user-${Date.now()}`, role: "user", text: messageText, at: new Date().toISOString() };
    chatMessages.push(message);
    broadcast({ type: "user_message", message });
    await pi.prompt(messageText);
    sendJson(response, 202, { accepted: true, message });
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
      if (browserInteractionMode !== "automatic" && new Set(["goto", "click", "fill", "select", "press"]).has(String(body.action))) {
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

async function shutdown() {
  clearInterval(heartbeat);
  pi.stop();
  if (studio.recorder.isActive()) await studio.stopRecording().catch(() => {});
  server.close();
}

process.on("SIGINT", () => void shutdown().finally(() => process.exit(0)));
process.on("SIGTERM", () => void shutdown().finally(() => process.exit(0)));

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
  if (error.code === "EADDRINUSE") runtimeLog("ERROR", `Port ${port} is already in use. Open ${origin} if Studio is already running.`);
  else runtimeLog("ERROR", `Studio server failed: ${errorMessage(error)}`);
  process.exitCode = 1;
});

await logStartupEnvironment();

server.listen(port, host, async () => {
  runtimeLog("READY", `Pi Business Skill Studio is ready at ${origin}`);
  if (["1", "true", "yes"].includes(String(process.env.BSS_OPEN_UI || "").toLowerCase()) && process.platform === "win32") {
    const opener = spawn("cmd.exe", ["/d", "/c", "start", "", origin], {
      detached: true,
      stdio: "ignore",
      windowsHide: true
    });
    opener.unref();
  }
  try {
    await pi.start();
  } catch (error) {
    runtimeLog("ERROR", `Pi failed to start: ${errorMessage(error)}`);
  }
});
