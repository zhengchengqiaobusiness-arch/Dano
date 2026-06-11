import * as fs from "node:fs";
import * as http from "node:http";
import * as path from "node:path";
import { ConversationController, HttpApiError } from "./http-command-adapter.js";
import { formatSseEvent } from "./sse-event-bus.js";
import type { ApiErrorResponse, ChatServerConfig } from "./types.js";

const MAX_JSON_BODY_BYTES = 64 * 1024;

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

export interface DanoHttpServerController {
  getUrl(): string;
  stop(): Promise<void>;
}

export function createHttpRequestHandler(
  controller: ConversationController,
  config: Pick<ChatServerConfig, "staticDir" | "heartbeatMs">,
): http.RequestListener {
  return async (req, res) => {
    try {
      await routeRequest(req, res, controller, config);
    } catch (error) {
      writeError(res, error);
    }
  };
}

export async function startHttpServer(
  controller: ConversationController,
  config: ChatServerConfig,
): Promise<DanoHttpServerController> {
  const server = http.createServer(
    createHttpRequestHandler(controller, {
      staticDir: config.staticDir,
      heartbeatMs: config.heartbeatMs,
    }),
  );

  await new Promise<void>((resolve, reject) => {
    const onError = (error: Error) => {
      server.off("listening", onListening);
      reject(error);
    };
    const onListening = () => {
      server.off("error", onError);
      resolve();
    };

    server.once("error", onError);
    server.once("listening", onListening);
    server.listen(config.port, config.host);
  });

  const address = server.address();
  const port = typeof address === "object" && address ? address.port : config.port;
  const host = config.host === "0.0.0.0" ? "127.0.0.1" : config.host;

  return {
    getUrl() {
      return `http://${host}:${port}`;
    },
    async stop() {
      await new Promise<void>((resolve, reject) => {
        server.close(error => {
          if (error) {
            reject(error);
            return;
          }
          resolve();
        });
      });
      await controller.dispose();
    },
  };
}

async function routeRequest(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  controller: ConversationController,
  config: Pick<ChatServerConfig, "staticDir" | "heartbeatMs">,
): Promise<void> {
  const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
  const pathname = url.pathname;

  if (req.method === "GET" && pathname === "/api/health") {
    writeJson(res, 200, { status: "ok" });
    return;
  }

  if (req.method === "POST" && pathname === "/api/conversations") {
    await readJsonBody(req);
    writeJson(res, 201, controller.createConversation());
    return;
  }

  const eventsMatch = /^\/api\/conversations\/([^/]+)\/events$/.exec(pathname);
  if (req.method === "GET" && eventsMatch?.[1]) {
    openEventStream(req, res, controller, decodeURIComponent(eventsMatch[1]), config);
    return;
  }

  const messagesMatch = /^\/api\/conversations\/([^/]+)\/messages$/.exec(pathname);
  if (req.method === "POST" && messagesMatch?.[1]) {
    const body = await readJsonBody(req);
    const result = await controller.sendMessage(
      decodeURIComponent(messagesMatch[1]),
      body,
    );
    writeJson(res, 202, result);
    return;
  }

  const retryMatch =
    /^\/api\/conversations\/([^/]+)\/messages\/([^/]+)\/retry$/.exec(pathname);
  if (req.method === "POST" && retryMatch?.[1] && retryMatch[2]) {
    await readJsonBody(req);
    const result = await controller.retryMessage(
      decodeURIComponent(retryMatch[1]),
      decodeURIComponent(retryMatch[2]),
    );
    writeJson(res, 202, result);
    return;
  }

  if (pathname.startsWith("/api/")) {
    throw new HttpApiError(404, "CONVERSATION_NOT_FOUND", "API route was not found.");
  }

  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("Method Not Allowed");
    return;
  }

  serveStatic(req, res, config.staticDir);
}

async function readJsonBody(req: http.IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;

  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > MAX_JSON_BODY_BYTES) {
      throw new HttpApiError(400, "INVALID_RESPONSE", "Request body is too large.");
    }
    chunks.push(buffer);
  }

  const text = Buffer.concat(chunks).toString("utf8").trim();
  if (!text) {
    return {};
  }

  try {
    const data = JSON.parse(text);
    return data && typeof data === "object" && !Array.isArray(data) ? data : {};
  } catch {
    throw new HttpApiError(400, "INVALID_RESPONSE", "Request body must be JSON.");
  }
}

function openEventStream(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  controller: ConversationController,
  conversationId: string,
  config: Pick<ChatServerConfig, "heartbeatMs">,
): void {
  if (!controller.hasConversation(conversationId)) {
    writeJson(res, 404, {
      code: "CONVERSATION_NOT_FOUND",
      errorMessage: "Conversation was not found.",
    } satisfies ApiErrorResponse);
    return;
  }

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
  res.flushHeaders?.();

  for (const event of controller.eventBus.getHistory(conversationId)) {
    res.write(formatSseEvent(event));
  }

  const unsubscribe = controller.eventBus.subscribe(conversationId, event => {
    res.write(formatSseEvent(event));
  });

  const heartbeat = setInterval(() => {
    res.write(formatSseEvent({ event: "heartbeat", data: {} }));
  }, config.heartbeatMs);

  req.on("close", () => {
    clearInterval(heartbeat);
    unsubscribe();
    res.end();
  });
}

function serveStatic(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  staticDir: string | undefined,
): void {
  if (!staticDir) {
    writePlaceholder(res);
    return;
  }

  const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
  const pathname = url.pathname === "/" ? "/index.html" : url.pathname;
  const decodedPath = decodeURIComponent(pathname);
  const safePath = path.normalize(decodedPath).replace(/^(\.\.(\/|\\|$))+/, "");
  const root = path.resolve(staticDir);
  const candidate = path.resolve(path.join(root, safePath));

  if (candidate !== root && !candidate.startsWith(`${root}${path.sep}`)) {
    res.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("Forbidden");
    return;
  }

  fs.stat(candidate, (error, stat) => {
    if (!error && stat.isFile()) {
      serveFile(req, res, candidate);
      return;
    }

    serveFile(req, res, path.join(root, "index.html"));
  });
}

function serveFile(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  filePath: string,
): void {
  fs.readFile(filePath, (error, data) => {
    if (error) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Not Found");
      return;
    }

    const contentType = MIME_TYPES[path.extname(filePath).toLowerCase()] ??
      "application/octet-stream";
    res.writeHead(200, {
      "Content-Type": contentType,
      "Cache-Control": "no-cache",
    });

    if (req.method === "HEAD") {
      res.end();
      return;
    }

    res.end(data);
  });
}

function writePlaceholder(res: http.ServerResponse): void {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Dano</title>
  </head>
  <body>
    <main>
      <h1>Dano bridge running</h1>
      <p>Build the web client to serve the browser UI.</p>
    </main>
  </body>
</html>`);
}

function writeJson(res: http.ServerResponse, status: number, data: unknown): void {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-cache",
  });
  res.end(JSON.stringify(data));
}

function writeError(res: http.ServerResponse, error: unknown): void {
  if (res.headersSent) {
    res.end();
    return;
  }

  if (error instanceof HttpApiError) {
    writeJson(res, error.status, error.toResponse());
    return;
  }

  const message = error instanceof Error ? error.message : String(error);
  writeJson(res, 500, {
    code: "LLM_UNAVAILABLE",
    errorMessage: message || "Internal server error.",
  } satisfies ApiErrorResponse);
}
