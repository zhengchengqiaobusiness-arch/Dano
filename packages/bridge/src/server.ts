/**
 * Bridge HTTP and SSE server.
 *
 * Handles:
 * - HTTP static file serving from config.staticDir
 * - HTTP command delivery from browser clients
 * - SSE fan-out for events, command responses, and extension UI requests
 * - Client tracking with monotonic sequence numbers
 */

import * as fs from "node:fs";
import * as http from "node:http";
import * as path from "node:path";
import type { BridgeEventBus } from "./bridge-event-bus.js";
import { getLanIps, isTailscaleIp } from "./network.js";
import type {
  BridgeConfig,
  BridgeEvent,
  ClientMessage,
  ServerMessage,
  WsClient,
} from "./types.js";

const MAX_JSON_BODY_BYTES = 1024 * 1024;

export interface RpcConnectionHandler {
  handleClientMessage(message: ClientMessage): void;
  dispose(): void;
}

export interface RpcConnectionContext {
  client: WsClient;
  config: BridgeConfig;
  eventBus: BridgeEventBus;
  emitEvent: (event: BridgeEvent) => void;
  send: (message: ServerMessage) => void;
}

export type RpcConnectionHandlerFactory = (
  ctx: RpcConnectionContext,
) => RpcConnectionHandler;

let clientSeqCounter = 0;

function generateClientId(): string {
  return `client_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

export class BridgeServer {
  private config: BridgeConfig;
  private handlerFactory: RpcConnectionHandlerFactory;
  private eventBus: BridgeEventBus;
  private emitEvent: (event: BridgeEvent) => void;

  private httpServer: http.Server | undefined;
  private handlers = new Map<string, RpcConnectionHandler>();
  private clients = new Map<string, WsClient>();

  private isRunning = false;
  private host: string = "localhost";
  private port: number = 0;

  constructor(
    config: BridgeConfig,
    handlerFactory: RpcConnectionHandlerFactory,
    eventBus: BridgeEventBus,
    emitEvent: (event: BridgeEvent) => void,
  ) {
    this.config = config;
    this.handlerFactory = handlerFactory;
    this.eventBus = eventBus;
    this.emitEvent = emitEvent;
  }

  async start(): Promise<{ host: string; port: number }> {
    if (this.isRunning) {
      throw new Error("Server is already running");
    }

    const startPort = this.config.port || 0;
    const maxPort = this.config.portMax || startPort;

    let boundPort = 0;
    let lastError: Error | undefined;

    for (
      let tryPort = startPort;
      tryPort <= maxPort || (startPort === 0 && tryPort === 0);
    ) {
      try {
        await this.bindToPort(tryPort);
        boundPort =
          tryPort === 0
            ? ((this.httpServer?.address() as { port: number })?.port ?? 0)
            : tryPort;
        break;
      } catch (err) {
        lastError = err instanceof Error ? err : new Error(String(err));
        if (startPort === 0) {
          throw lastError;
        }

        tryPort++;
        if (tryPort > maxPort) {
          throw new Error(
            `Failed to bind to any port in range ${startPort}-${maxPort}: ${lastError.message}`,
          );
        }
      }
    }

    this.host = this.config.host;
    this.port = boundPort;
    this.isRunning = true;

    this.emitEvent({
      type: "server_start",
      host: this.host,
      port: this.port,
    });

    return { host: this.host, port: this.port };
  }

  private bindToPort(port: number): Promise<void> {
    this.httpServer = http.createServer((req, res) => {
      void this.handleRequest(req, res);
    });

    return new Promise((resolve, reject) => {
      const server = this.httpServer!;

      const onError = (err: Error) => {
        server.off("error", onError);
        server.off("listening", onListening);
        reject(err);
      };

      const onListening = () => {
        server.off("error", onError);
        server.off("listening", onListening);
        resolve();
      };

      server.once("error", onError);
      server.once("listening", onListening);
      server.listen(port, this.config.host);
    });
  }

  async stop(): Promise<void> {
    if (!this.isRunning) {
      return;
    }

    for (const [clientId, handler] of this.handlers) {
      handler.dispose();
      this.eventBus.unregisterClient(clientId);
    }
    this.handlers.clear();
    this.clients.clear();

    if (this.httpServer) {
      await new Promise<void>((resolve, reject) => {
        this.httpServer?.close(err => {
          if (err) reject(err);
          else resolve();
        });
      });
      this.httpServer = undefined;
    }

    this.isRunning = false;
    this.port = 0;

    this.emitEvent({ type: "server_stop" });
  }

  getIsRunning(): boolean {
    return this.isRunning;
  }

  getAddress(): { host: string; port: number } | undefined {
    if (!this.isRunning) return undefined;
    return { host: this.host, port: this.port };
  }

  getClientCount(): number {
    return this.clients.size;
  }

  getClients(): WsClient[] {
    return Array.from(this.clients.values());
  }

  private async handleRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse,
  ): Promise<void> {
    try {
      const url = new URL(req.url || "/", `http://${req.headers.host}`);
      const pathname = url.pathname;

      if (req.method === "GET" && pathname === "/api/health") {
        writeJson(res, 200, { status: "ok" });
        return;
      }

      if (req.method === "POST" && pathname === "/api/clients") {
        await readJsonBody(req);
        this.createClient(res);
        return;
      }

      const clientEventsMatch = /^\/api\/clients\/([^/]+)\/events$/.exec(
        pathname,
      );
      if (req.method === "GET" && clientEventsMatch?.[1]) {
        this.openEventStream(req, res, decodeURIComponent(clientEventsMatch[1]));
        return;
      }

      const clientMessagesMatch = /^\/api\/clients\/([^/]+)\/messages$/.exec(
        pathname,
      );
      if (req.method === "POST" && clientMessagesMatch?.[1]) {
        const body = await readJsonBody(req);
        this.handleClientMessage(
          res,
          decodeURIComponent(clientMessagesMatch[1]),
          body,
        );
        return;
      }

      const clientDisconnectMatch = /^\/api\/clients\/([^/]+)\/disconnect$/.exec(
        pathname,
      );
      if (
        (req.method === "POST" || req.method === "DELETE") &&
        clientDisconnectMatch?.[1]
      ) {
        await readJsonBody(req);
        this.disconnectClient(decodeURIComponent(clientDisconnectMatch[1]));
        writeJson(res, 202, { status: "disconnected" });
        return;
      }

      if (pathname.startsWith("/api/")) {
        writeJson(res, 404, { error: "API route was not found" });
        return;
      }

      this.handleStaticRequest(req, res);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      writeJson(res, 500, { error: message || "Internal Server Error" });
    }
  }

  private createClient(res: http.ServerResponse): void {
    clientSeqCounter++;
    const client: WsClient = {
      id: generateClientId(),
      seq: clientSeqCounter,
      connectedAt: new Date().toISOString(),
    };

    this.clients.set(client.id, client);
    this.eventBus.registerClient(client);

    const handler = this.handlerFactory({
      client,
      config: this.config,
      eventBus: this.eventBus,
      emitEvent: this.emitEvent,
      send: message => {
        this.eventBus.sendToClient(client.id, message);
      },
    });
    this.handlers.set(client.id, handler);

    this.emitEvent({
      type: "client_connect",
      client,
    });

    writeJson(res, 201, {
      client,
      eventsUrl: `/api/clients/${encodeURIComponent(client.id)}/events`,
      messagesUrl: `/api/clients/${encodeURIComponent(client.id)}/messages`,
      defaultWorkspacePath: this.config.defaultWorkspacePath,
    });
  }

  private openEventStream(
    req: http.IncomingMessage,
    res: http.ServerResponse,
    clientId: string,
  ): void {
    if (!this.clients.has(clientId)) {
      writeJson(res, 404, { error: "Client was not found" });
      return;
    }

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });
    res.flushHeaders?.();

    const send = (message: ServerMessage) => {
      res.write(formatSseMessage(message));
    };
    const unregister = this.eventBus.connectClient(clientId, send);
    const heartbeat = setInterval(() => {
      res.write(": heartbeat\n\n");
    }, this.config.heartbeatInterval);

    req.on("close", () => {
      clearInterval(heartbeat);
      unregister();
      res.end();
    });
  }

  private handleClientMessage(
    res: http.ServerResponse,
    clientId: string,
    body: unknown,
  ): void {
    const handler = this.handlers.get(clientId);
    if (!handler) {
      writeJson(res, 404, { error: "Client was not found" });
      return;
    }

    if (!isClientMessage(body)) {
      writeJson(res, 400, { error: "Request body must be a client message" });
      return;
    }

    handler.handleClientMessage(body);
    writeJson(res, 202, { status: "accepted" });
  }

  private disconnectClient(clientId: string): void {
    const handler = this.handlers.get(clientId);
    const client = this.clients.get(clientId);
    if (!handler || !client) return;

    handler.dispose();
    this.handlers.delete(clientId);
    this.clients.delete(clientId);
    this.eventBus.unregisterClient(clientId);
  }

  private handleStaticRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse,
  ): void {
    if (req.method !== "GET" && req.method !== "HEAD") {
      res.writeHead(405, { "Content-Type": "text/plain" });
      res.end("Method Not Allowed");
      return;
    }

    const url = new URL(req.url || "/", `http://${req.headers.host}`);
    const pathname = url.pathname === "/" ? "/index.html" : url.pathname;
    const safePath = path.normalize(pathname).replace(/^(\.\.(\/|\\|$))+/, "");

    if (!this.config.staticDir) {
      if (safePath === "/index.html") {
        res.writeHead(200, { "Content-Type": "text/html" });
        res.end(getPlaceholderHtml(this.host, this.port));
      } else {
        res.writeHead(404, { "Content-Type": "text/plain" });
        res.end("Not Found - No web bundle configured");
      }
      return;
    }

    const root = path.resolve(this.config.staticDir);
    const filePath = path.resolve(path.join(root, safePath));
    if (filePath !== root && !filePath.startsWith(`${root}${path.sep}`)) {
      res.writeHead(403, { "Content-Type": "text/plain" });
      res.end("Forbidden");
      return;
    }

    fs.stat(filePath, (err, stats) => {
      if (err || !stats.isFile()) {
        const indexPath = path.join(this.config.staticDir!, "index.html");
        fs.stat(indexPath, (indexErr, indexStats) => {
          if (indexErr || !indexStats.isFile()) {
            res.writeHead(404, { "Content-Type": "text/plain" });
            res.end("Not Found");
            return;
          }
          this.serveFile(req, res, indexPath);
        });
        return;
      }

      this.serveFile(req, res, filePath);
    });
  }

  private serveFile(
    req: http.IncomingMessage,
    res: http.ServerResponse,
    filePath: string,
  ): void {
    const ext = path.extname(filePath).toLowerCase();
    const contentType = MIME_TYPES[ext] || "application/octet-stream";

    fs.readFile(filePath, (err, data) => {
      if (err) {
        res.writeHead(500, { "Content-Type": "text/plain" });
        res.end("Internal Server Error");
        return;
      }

      const body =
        contentType === "text/html"
          ? injectRuntimeConfig(data.toString("utf8"))
          : data;

      res.writeHead(200, {
        "Content-Type": contentType,
        "Cache-Control": "no-cache",
      });
      if (req.method === "HEAD") {
        res.end();
        return;
      }
      res.end(body);
    });
  }
}

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".mjs": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".otf": "font/otf",
  ".eot": "application/vnd.ms-fontobject",
};

async function readJsonBody(req: http.IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;

  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > MAX_JSON_BODY_BYTES) {
      throw new Error("Request body is too large");
    }
    chunks.push(buffer);
  }

  const text = Buffer.concat(chunks).toString("utf8").trim();
  if (!text) {
    return {};
  }

  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new Error("Request body must be JSON");
  }
}

function isClientMessage(value: unknown): value is ClientMessage {
  if (!value || typeof value !== "object") return false;
  const data = value as Partial<ClientMessage>;
  if (data.type === "command") {
    return Boolean(data.payload && typeof data.payload === "object");
  }
  if (data.type === "extension_ui_response") {
    return Boolean(data.payload && typeof data.payload === "object");
  }
  return false;
}

function formatSseMessage(message: ServerMessage): string {
  const lines = ["event: message"];
  const data = JSON.stringify(message);
  for (const line of data.split(/\r?\n/)) {
    lines.push(`data: ${line}`);
  }
  return `${lines.join("\n")}\n\n`;
}

function writeJson(
  res: http.ServerResponse,
  status: number,
  data: unknown,
): void {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-cache",
  });
  res.end(JSON.stringify(data));
}

function runtimeDebugModeEnabled(): boolean {
  const value = process.env.PI_WEB_DEBUG;
  if (typeof value !== "string") return false;
  const normalized = value.trim().toLowerCase();
  return normalized === "1" || normalized === "true";
}

function injectRuntimeConfig(html: string): string {
  const configScript = `<script>window.__PI_WEB_CONFIG__=${JSON.stringify({ debugModeAvailable: runtimeDebugModeEnabled() })};</script>`;
  return html.includes("</head>")
    ? html.replace("</head>", `${configScript}</head>`)
    : `${configScript}${html}`;
}

function getPlaceholderHtml(_host: string, port: number): string {
  const lanIps = getLanIps();
  const httpUrl = (ip: string) => `http://${ip}:${port}`;
  const lanUrlLines =
    lanIps.length > 0
      ? lanIps
          .map(ip => {
            const label = isTailscaleIp(ip) ? " Tailscale" : "";
            return `<span class="code">${httpUrl(ip)}</span>${label}`;
          })
          .join("<br>\n\t\t\t")
      : "";

  return `<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title>Pi Web Bridge</title>
	<style>
		body {
			font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
			max-width: 800px;
			margin: 50px auto;
			padding: 20px;
			line-height: 1.6;
			color: #333;
		}
		h1 { color: #2563eb; }
		.status { background: #f0fdf4; border: 1px solid #86efac; padding: 15px; border-radius: 6px; margin: 20px 0; }
		.lan-info { background: #ecfdf5; padding: 15px; border-radius: 6px; margin: 20px 0; }
		.code { font-family: 'SF Mono', Monaco, monospace; background: #f3f4f6; padding: 2px 6px; border-radius: 3px; }
	</style>
</head>
<body>
	<h1>Pi Web Bridge</h1>
	<div class="status">
		<strong>Bridge server is running</strong><br>
		HTTP/SSE transport: <span class="code">/api/clients</span>
	</div>
	${
    lanIps.length > 0
      ? `<div class="lan-info">
		<strong>LAN Access:</strong><br>
			${lanUrlLines}
	</div>`
      : ""
  }
	<p>No web bundle is configured. Build the web UI or pass a staticDir.</p>
</body>
</html>`;
}
