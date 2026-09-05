import { spawn } from "node:child_process";
import { timingSafeEqual } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import http, { type IncomingMessage, type ServerResponse } from "node:http";
import path from "node:path";
import { chromium } from "playwright";
import { loadConfig } from "../config.js";
import { formatProcessLog, killCommandLineMatches } from "../process-lifecycle.js";
import { StudioService } from "../studio-service.js";
import type { OperationKind } from "../domain.js";
import { isPageSessionId, sendEvent, WorkbenchPage } from "./workbench-page.js";

const host = "127.0.0.1";
const port = Number(process.env.BSS_PORT || 4310);
const origin = `http://${host}:${port}`;
const publicDir = path.resolve(process.cwd(), "web");
const sharedConfig = { ...loadConfig(), headless: true };
const studio = new StudioService(sharedConfig);

type BrowserInteractionMode = "manual" | "automatic";
type RuntimeLogLevel = "PLAIN" | "CHECK" | "START" | "INFO" | "READY" | "BROWSER" | "PI" | "TOOL" | "WAIT" | "WARN" | "ERROR" | "PROCESS";
const pages = new Map<string, WorkbenchPage>();
const pagesByToken = new Map<string, WorkbenchPage>();
const secretValues = Object.entries(process.env)
  .filter(([key, value]) => /KEY|TOKEN|SECRET|PASSWORD/i.test(key) && typeof value === "string" && value.length >= 6)
  .map(([, value]) => value as string)
  .sort((left, right) => right.length - left.length);

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

function broadcastAll(payload: unknown) {
  for (const page of pages.values()) page.broadcast(payload);
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

function parseExpectedOperations(value: unknown): OperationKind[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new Error("expectedOperations 必须是操作类型数组");
  const allowed = new Set<OperationKind>(["query", "create", "update", "review", "delete", "upload", "download", "action"]);
  const operations = value.filter((item): item is OperationKind => typeof item === "string" && allowed.has(item as OperationKind));
  if (operations.length !== value.length) throw new Error("expectedOperations 包含不支持的操作类型");
  return [...new Set(operations)];
}

function parseViewport(value: unknown) {
  if (!value || typeof value !== "object") return undefined;
  const width = Number((value as { width?: unknown }).width);
  const height = Number((value as { height?: unknown }).height);
  const scale = Number((value as { scale?: unknown }).scale);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return undefined;
  return {
    width,
    height,
    ...(Number.isFinite(scale) ? { scale } : {})
  };
}

function forgetPage(page: WorkbenchPage) {
  pages.delete(page.id);
  pagesByToken.delete(page.browserToken);
}

function getOrCreatePage(id: string) {
  const existing = pages.get(id);
  if (existing) {
    existing.touch();
    return existing;
  }
  const page = new WorkbenchPage(id, sharedConfig, origin, sanitizeTranscript, runtimeLog, gone => forgetPage(gone));
  pages.set(id, page);
  pagesByToken.set(page.browserToken, page);
  runtimeLog("PROCESS", formatProcessLog("OPEN", "workbench-page", { page: id }));
  runtimeLog("PI", `Opened isolated workbench page ${id}.`);
  return page;
}

function pageSessionIdFrom(request: IncomingMessage) {
  const url = new URL(request.url || "/", origin);
  return String(request.headers["x-bss-page-session"] || url.searchParams.get("pageSession") || "").trim();
}

function requirePage(request: IncomingMessage) {
  const id = pageSessionIdFrom(request);
  if (!isPageSessionId(id)) throw new Error("A page session is required");
  return getOrCreatePage(id);
}

function pageFromBrowserToken(request: IncomingMessage) {
  const supplied = request.headers.authorization?.replace(/^Bearer\s+/i, "") || "";
  const actual = Buffer.from(supplied);
  for (const [token, page] of pagesByToken) {
    const expected = Buffer.from(token);
    if (actual.length === expected.length && timingSafeEqual(actual, expected)) return page;
  }
}

async function handleApi(request: IncomingMessage, response: ServerResponse, pathname: string) {
  if (request.method === "GET" && pathname === "/api/events") {
    const page = requirePage(request);
    response.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no"
    });
    response.socket?.setNoDelay(true);
    response.write("retry: 1500\n\n");
    page.cancelAbandon();
    page.clients.add(response);
    sendEvent(response, { type: "connected", pageSession: page.id, epoch: page.epoch });
    void page.ensureStarted().catch(error => {
      runtimeLog("WARN", `Failed to prestart Pi for ${page.id}: ${errorMessage(error)}`);
    });
    request.on("close", () => {
      page.clients.delete(response);
      if (page.clients.size === 0) page.scheduleAbandon("sse-disconnected");
    });
    return;
  }

  if (request.method === "GET" && pathname === "/api/status") {
    const page = requirePage(request);
    sendJson(response, 200, {
      pageSession: page.id,
      epoch: page.epoch,
      agent: page.pi.status(),
      browser: await page.browserState(),
      model: process.env.PI_MODEL || null,
      provider: process.env.PI_PROVIDER || "xiaomi-token-plan-cn",
      thinking: process.env.PI_THINKING || "medium",
      sessionItems: page.transcript.items
    });
    return;
  }

  if (request.method === "GET" && pathname === "/api/browser/state") {
    const page = requirePage(request);
    sendJson(response, 200, await page.browserState());
    return;
  }

  if (request.method === "GET" && pathname === "/api/browser/frame") {
    const page = requirePage(request);
    if (!page.recorder.isActive()) {
      response.writeHead(204, { "Cache-Control": "no-store" }).end();
      return;
    }
    try {
      const frame = await page.recorder.preview();
      response.writeHead(200, {
        "Content-Type": "image/jpeg",
        "Content-Length": String(frame.byteLength),
        "Cache-Control": "no-store, max-age=0"
      });
      response.end(frame);
    } catch {
      response.writeHead(204, { "Cache-Control": "no-store" }).end();
    }
    return;
  }

  if (request.method === "GET" && pathname === "/api/skills") {
    sendJson(response, 200, { skills: await studio.listSkills() });
    return;
  }

  if (request.method === "POST" && pathname === "/api/skills/export") {
    const body = await readJsonBody(request);
    if (typeof body.name !== "string" || !body.name.trim()) throw new Error("请输入 Skill 名称");
    const pageId = pageSessionIdFrom(request);
    const workbench = isPageSessionId(pageId) ? pages.get(pageId) : undefined;
    const sessionId = typeof body.sessionId === "string" && body.sessionId.trim()
      ? body.sessionId.trim()
      : workbench?.lastRecordingSessionId;
    const record = await studio.exportManaged(body.name, body.confirmed === true, sessionId);
    sendJson(response, 200, record);
    broadcastAll({ type: "skills_changed" });
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
      broadcastAll({ type: "skills_changed" });
      return;
    }
    if (request.method === "DELETE" && action === "delete") {
      const record = await studio.deleteSkill(name, body.confirmed === true);
      sendJson(response, 200, record);
      broadcastAll({ type: "skills_changed" });
      return;
    }
    if (request.method === "POST" && action === "invoke") {
      const page = requirePage(request);
      const invocation = await studio.invokeSkill(name, typeof body.goal === "string" ? body.goal : "");
      const userEvent = page.acceptUserMessage(invocation.prompt);
      sendJson(response, 202, { accepted: true, skill: invocation.record.name, item: userEvent.item, epoch: page.epoch });
      void page.runPrompt(invocation.prompt);
      return;
    }
  }

  if (request.method === "POST" && pathname === "/api/browser/open") {
    const page = requirePage(request);
    const body = await readJsonBody(request);
    if (body.mode !== undefined) page.setMode(parseBrowserMode(body.mode));
    const session = await page.startRecording(
      parseBrowserUrl(body.url),
      body.name,
      parseViewport(body.viewport) || page.preferredViewport,
      parseExpectedOperations(body.expectedOperations),
      body.completeFieldCoverage === true,
      body.completePageCoverage === true
    );
    runtimeLog("BROWSER", `${page.mode === "manual" ? "Manual" : "Pi automatic"} recording session started on ${page.id}.`);
    sendJson(response, 200, { session, state: await page.browserState() });
    page.broadcast({ type: "browser_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/browser/mode") {
    const page = requirePage(request);
    const body = await readJsonBody(request);
    page.setMode(parseBrowserMode(body.mode));
    sendJson(response, 200, await page.browserState());
    page.broadcast({ type: "browser_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/browser/manual") {
    const page = requirePage(request);
    const body = await readJsonBody(request);
    const result = await page.recorder.manualControl(body) as { observed?: { eventType?: string; label?: string; name?: string; text?: string; value?: unknown; selector?: string } };
    const liveDrag = body.action === "drag" && (body.phase === "start" || body.phase === "move");
    const dragEnd = Array.isArray(body.points) ? body.points[body.points.length - 1] : undefined;
    const observed = liveDrag ? undefined : result.observed || (body.action === "click"
      ? { eventType: "click", label: "页面", value: `${body.x},${body.y}` }
      : body.action === "drag"
        ? { eventType: "click", label: "页面", value: `${dragEnd?.x ?? body.toX ?? body.x},${dragEnd?.y ?? body.toY ?? body.y}` }
      : undefined);
    if (observed && body.action !== "scroll") {
      const label = observed.label || observed.text || observed.name || (body.action === "click" || body.action === "drag" ? "页面" : "页面字段");
      runtimeLog("BROWSER", `Manual ${observed.eventType || body.action || "action"}: ${label}=${String(observed.value ?? "")}`);
      if (page.transcriptOpen) page.broadcastSession(page.transcript.addManual(observed));
    }
    sendJson(response, 200, { ...result, observed, recorded: Boolean(observed && body.action !== "scroll") });
    return;
  }

  if (request.method === "POST" && pathname === "/api/browser/takeover/complete") {
    const page = requirePage(request);
    const body = await readJsonBody(request);
    const completed = typeof body.id === "string" && page.completeManualTakeover(body.id);
    if (!completed) throw new Error("人工接管已结束或接管标识无效，请刷新页面状态");
    sendJson(response, 200, { completed: true, state: await page.browserState() });
    page.broadcast({ type: "browser_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/browser/viewport") {
    const page = requirePage(request);
    const size = await page.rememberViewport(parseViewport(await readJsonBody(request)));
    sendJson(response, 200, { viewport: size, state: await page.browserState() });
    return;
  }

  if (request.method === "POST" && pathname === "/api/browser/reload") {
    const page = requirePage(request);
    const result = await page.recorder.reload();
    runtimeLog("BROWSER", "Embedded browser page reloaded.");
    sendJson(response, 200, result);
    page.broadcast({ type: "browser_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/browser/stop") {
    const page = requirePage(request);
    const readiness = await page.recorder.stopReadiness();
    if (!readiness.ready) {
      sendJson(response, 409, { error: readiness.message, ...readiness, stopped: false });
      return;
    }
    const session = await page.stopRecording();
    runtimeLog("BROWSER", "Recording session stopped and evidence was saved.");
    sendJson(response, 200, session);
    page.broadcast({ type: "browser_changed" });
    return;
  }

  if (request.method === "POST" && pathname === "/api/session/clear") {
    const page = requirePage(request);
    await page.reset();
    runtimeLog("PI", `Page ${page.id} ended its recording and started a new conversation.`);
    sendJson(response, 200, { cleared: true, epoch: page.epoch, pageSession: page.id, sessionItems: page.transcript.items });
    return;
  }

  if ((request.method === "POST" || request.method === "GET") && pathname === "/api/session/leave") {
    const id = pageSessionIdFrom(request);
    const page = isPageSessionId(id) ? pages.get(id) : undefined;
    if (page) page.scheduleAbandon("page-closed");
    sendJson(response, 200, { left: true, pageSession: id || null });
    return;
  }

  if (request.method === "POST" && pathname === "/api/chat") {
    const page = requirePage(request);
    const body = await readJsonBody(request);
    const messageText = typeof body.message === "string" ? body.message.trim() : "";
    if (!messageText) throw new Error("A message is required");
    const userEvent = page.acceptUserMessage(messageText);
    sendJson(response, 202, { accepted: true, item: userEvent.item, epoch: page.epoch });
    void page.runPrompt(messageText);
    return;
  }

  if (request.method === "POST" && pathname === "/api/agent/abort") {
    const page = requirePage(request);
    await page.abortWork("abort");
    sendJson(response, 200, { aborted: true });
    return;
  }

  if (request.method === "POST" && pathname === "/api/agent/ui-response") {
    const page = requirePage(request);
    const body = await readJsonBody(request);
    page.pi.respondToUi(body);
    sendJson(response, 200, { accepted: true });
    return;
  }

  if (pathname.startsWith("/internal/browser/")) {
    const page = pageFromBrowserToken(request);
    if (!page) {
      sendJson(response, 401, { error: "Unauthorized" });
      return;
    }
    const body = await readJsonBody(request);
    if (request.method === "POST" && pathname === "/internal/browser/start") {
      if (page.mode !== "automatic") throw new Error("当前是手动录制模式；请在前端切换到 Pi 自动点击后再让 Pi 启动浏览器");
      const url = parseBrowserUrl(body.url);
      const gate = await studio.evaluateRerecord(url);
      if (!gate.allowed && page.lastRecordingSessionId) {
        sendJson(response, 200, { blocked: true, started: false, message: gate.message });
        return;
      }
      sendJson(response, 200, await page.startRecording(
        url,
        body.name,
        parseViewport(body.viewport) || page.preferredViewport,
        parseExpectedOperations(body.expectedOperations),
        body.completeFieldCoverage === true,
        body.completePageCoverage === true
      ));
      page.broadcast({ type: "browser_changed" });
      return;
    }
    if (request.method === "POST" && pathname === "/internal/browser/stop") {
      if (page.mode !== "automatic") throw new Error("当前是手动录制模式，Pi 不能停止手动会话");
      sendJson(response, 200, await page.stopRecording());
      page.broadcast({ type: "browser_changed" });
      return;
    }
    if (request.method === "POST" && pathname === "/internal/browser/stop-readiness") {
      sendJson(response, 200, await page.recorder.stopReadiness());
      return;
    }
    if (request.method === "POST" && pathname === "/internal/browser/inspect") {
      if (typeof body.selector !== "string" || !body.selector) throw new Error("A selector is required");
      sendJson(response, 200, await page.recorder.inspectTarget(body.selector));
      return;
    }
    if (request.method === "POST" && pathname === "/internal/browser/control") {
      if (page.mode !== "automatic" && new Set(["goto", "next-page", "click", "fill", "select", "choose", "press", "exercise-form", "submit-form"]).has(String(body.action))) {
        throw new Error("当前是手动录制模式；Pi 只能读取页面，不能自动点击或输入");
      }
      const result = await page.recorder.control(body) as {
        ok?: boolean;
        stopped?: boolean;
        followManualSteps?: boolean;
        reason?: string;
      };
      if (result?.stopped) {
        const reason = result.reason || "自动操作连续失败 3 次，请人工完成当前页面后继续";
        page.broadcast({ type: "browser_changed" });
        const takeover = await page.requestManualTakeover(reason);
        if (takeover.completed) {
          const snapshot = await page.recorder.control({ action: "snapshot" });
          sendJson(response, 200, {
            ...result,
            ok: true,
            stopped: false,
            followManualSteps: false,
            resumedAfterManualTakeover: true,
            snapshot
          });
        } else {
          sendJson(response, 200, { ...result, manualTakeoverCancelled: true });
        }
        return;
      }
      sendJson(response, 200, result);
      page.broadcast({ type: "browser_changed" });
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
  for (const page of pages.values()) {
    for (const client of page.clients) client.write(": heartbeat\n\n");
  }
}, 15_000);

let shuttingDown = false;

async function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  runtimeLog("PROCESS", formatProcessLog("CLOSE", "studio-server", { pid: process.pid, reason: "signal" }));
  clearInterval(heartbeat);
  try { broadcastAll({ type: "studio_shutdown" }); } catch { /* clients may already be gone */ }
  await Promise.race([
    Promise.allSettled([...pages.values()].map(page => page.dispose("studio-shutdown"))),
    new Promise<void>(resolve => setTimeout(resolve, 8_000))
  ]);
  pages.clear();
  pagesByToken.clear();
  await studio.recorder.disposeAndKill("studio-shutdown");
  const leftoverLog = (level: "PROCESS" | "WARN", message: string) => runtimeLog(level, message);
  await killCommandLineMatches(sharedConfig.profileDir, leftoverLog);
  await killCommandLineMatches(path.join(sharedConfig.rootDir, "node_modules", "@earendil-works", "pi-coding-agent"), leftoverLog);
  try { server.close(); } catch { /* already closed */ }
  process.exit(0);
}

process.on("SIGINT", () => { void shutdown(); });
process.on("SIGTERM", () => { void shutdown(); });
process.on("SIGHUP", () => { void shutdown(); });
process.on("SIGBREAK", () => { void shutdown(); });

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
  runtimeLog("PROCESS", formatProcessLog("OPEN", "studio-server", { pid: process.pid }));
  runtimeLog("READY", `Pi Business Skill Studio is ready at ${origin}`);
  if (["1", "true", "yes"].includes(String(process.env.BSS_OPEN_UI || "").toLowerCase()) && process.platform === "win32") {
    spawn("cmd.exe", ["/d", "/c", "start", "", origin], {
      stdio: "ignore",
      windowsHide: true
    });
  }
  runtimeLog("INFO", "Each workbench tab keeps its own conversation; refresh reuses that tab, a new page starts clean.");
});
