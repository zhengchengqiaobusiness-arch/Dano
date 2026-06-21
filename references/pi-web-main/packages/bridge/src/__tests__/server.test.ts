import * as http from "node:http";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WebSocket } from "ws";
import { BridgeEventBus } from "../bridge-event-bus.js";
import {
  BridgeServer,
  type WsConnectionHandler,
  type WsConnectionHandlerFactory,
} from "../server.js";
import { DEFAULT_BRIDGE_CONFIG, type BridgeEvent } from "../types.js";

const waitForAsyncWork = (ms = 100) =>
  new Promise(resolve => setTimeout(resolve, ms));

const requestText = (
  url: string,
  options?: http.RequestOptions & { cookies?: Record<string, string> },
): Promise<{
  status: number;
  body: string;
  headers: http.IncomingHttpHeaders;
}> =>
  new Promise((resolve, reject) => {
    const parsedUrl = new URL(url);
    const opts: http.RequestOptions = {
      hostname: parsedUrl.hostname,
      port: parsedUrl.port,
      path: parsedUrl.pathname + parsedUrl.search,
      method: options?.method ?? "GET",
      headers: {} as Record<string, string>,
    };
    if (options?.cookies) {
      (opts.headers as Record<string, string>)["Cookie"] = Object.entries(
        options.cookies,
      )
        .map(([k, v]) => `${k}=${v}`)
        .join("; ");
    }
    if (options?.headers) {
      Object.assign(opts.headers as Record<string, string>, options.headers);
    }
    const request = http.request(opts, response => {
      let body = "";
      response.on("data", chunk => {
        body += chunk;
      });
      response.on("end", () => {
        resolve({
          status: response.statusCode ?? 0,
          body,
          headers: response.headers,
        });
      });
    });
    request.on("error", reject);
    request.setTimeout(5000, () => {
      request.destroy();
      reject(new Error("request timeout"));
    });
    request.end();
  });

describe("BridgeServer", () => {
  const createMockHandler = (): WsConnectionHandler => ({
    dispose: vi.fn(),
  });

  const createMockHandlerFactory = (
    handlers: WsConnectionHandler[],
  ): WsConnectionHandlerFactory => {
    return _ctx => {
      const handler = createMockHandler();
      handlers.push(handler);
      return handler;
    };
  };

  let eventBus: BridgeEventBus;
  let handlerFactory: WsConnectionHandlerFactory;
  let createdHandlers: WsConnectionHandler[];
  let events: BridgeEvent[];

  beforeEach(() => {
    eventBus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
    createdHandlers = [];
    handlerFactory = createMockHandlerFactory(createdHandlers);
    events = [];
  });

  afterEach(() => {
    eventBus.dispose();
  });

  describe("lifecycle", () => {
    it("starts on an available port and emits server_start", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );

      const address = await server.start();

      expect(server.getIsRunning()).toBe(true);
      expect(address.port).toBeGreaterThan(0);
      expect(events).toContainEqual({
        type: "server_start",
        host: "0.0.0.0",
        port: address.port,
      });

      await server.stop();
    });

    it("rejects a second start while already running", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );

      await server.start();
      await expect(server.start()).rejects.toThrow("Server is already running");

      await server.stop();
    });

    it("stops gracefully and clears its address", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );

      await server.start();
      await server.stop();

      expect(server.getIsRunning()).toBe(false);
      expect(server.getAddress()).toBeUndefined();
      expect(events).toContainEqual({ type: "server_stop" });
    });

    it("stops even when a browser websocket is still connected", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );

      const address = await server.start();
      const ws = new WebSocket(`ws://localhost:${address.port}/ws`);

      await new Promise<void>((resolve, reject) => {
        ws.once("open", () => resolve());
        ws.once("error", reject);
      });

      const closedPromise = new Promise<void>(resolve => {
        ws.once("close", () => resolve());
      });

      await Promise.race([
        server.stop(),
        new Promise((_, reject) => {
          setTimeout(() => reject(new Error("server stop timed out")), 1500);
        }),
      ]);
      await closedPromise;

      expect(server.getIsRunning()).toBe(false);
      expect(server.getAddress()).toBeUndefined();
      expect(ws.readyState).toBe(WebSocket.CLOSED);
      expect(events).toContainEqual({ type: "server_stop" });
    });

    it("can restart after a full stop", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );

      const first = await server.start();
      await server.stop();
      const second = await server.start();

      expect(first.port).toBeGreaterThan(0);
      expect(second.port).toBeGreaterThan(0);
      expect(server.getIsRunning()).toBe(true);

      await server.stop();
    });
  });

  describe("port fallback", () => {
    it("falls back within the configured port range when the preferred port is taken", async () => {
      const occupiedServer = http.createServer((_req, res) => {
        res.writeHead(200);
        res.end("occupied");
      });
      await new Promise<void>(resolve =>
        occupiedServer.listen(0, "127.0.0.1", () => resolve()),
      );

      const address = occupiedServer.address();
      if (!address || typeof address === "string") {
        throw new Error("failed to get occupied port");
      }

      const preferredPort = address.port;
      const server = new BridgeServer(
        {
          ...DEFAULT_BRIDGE_CONFIG,
          host: "127.0.0.1",
          port: preferredPort,
          portMax: preferredPort + 3,
        },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );

      try {
        const bridgeAddress = await server.start();
        expect(bridgeAddress.port).not.toBe(preferredPort);
        expect(bridgeAddress.port).toBeGreaterThan(preferredPort);
        expect(bridgeAddress.port).toBeLessThanOrEqual(preferredPort + 3);
      } finally {
        await server.stop();
        await new Promise<void>((resolve, reject) => {
          occupiedServer.close(error => {
            if (error) reject(error);
            else resolve();
          });
        });
      }
    });

    it("uses an OS-assigned port when configured with port 0", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );

      const address = await server.start();
      expect(address.port).toBeGreaterThan(0);
      expect(server.getAddress()).toEqual({
        host: "0.0.0.0",
        port: address.port,
      });

      await server.stop();
    });
  });

  describe("HTTP access", () => {
    it("serves HTTP GET without requiring a token", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );
      const address = await server.start();

      const response = await requestText(`http://localhost:${address.port}/`);
      expect(response.status).toBe(200);
      expect(response.body).toContain("Pi Web Bridge");
      expect(response.headers["set-cookie"]).toBeUndefined();

      await server.stop();
    });

    it("ignores legacy token query params and cookies", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );
      const address = await server.start();

      const response = await requestText(
        `http://localhost:${address.port}/?token=legacy`,
        { cookies: { pi_token: "wrong-token" } },
      );
      expect(response.status).toBe(200);
      expect(response.body).toContain("Pi Web Bridge");
      expect(response.headers["set-cookie"]).toBeUndefined();

      await server.stop();
    });
  });

  describe("HTTP static file serving", () => {
    it("serves placeholder HTML at the root when no staticDir is configured", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );
      const address = await server.start();

      const response = await requestText(
        `http://localhost:${address.port}/?token=legacy`,
      );
      expect(response.status).toBe(200);
      expect(response.body).toContain("Pi Web Bridge");
      expect(response.body).toContain(`http://localhost:${address.port}`);

      await server.stop();
    });

    it("returns 404 for unknown files when no staticDir is configured", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );
      const address = await server.start();

      const response = await requestText(
        `http://localhost:${address.port}/some-file.js`,
      );
      expect(response.status).toBe(404);
      expect(response.body).toContain("Not Found");

      await server.stop();
    });

    it("rejects non-GET methods", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );
      const address = await server.start();

      const response = await requestText(
        `http://localhost:${address.port}/?token=legacy`,
        {
          method: "POST",
        },
      );
      expect(response.status).toBe(405);
      expect(response.body).toContain("Method Not Allowed");

      await server.stop();
    });
  });

  describe("WS connections", () => {
    it("accepts WS connect without a token", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );
      const address = await server.start();

      const ws = new WebSocket(`ws://localhost:${address.port}/ws`);
      await new Promise<void>((resolve, reject) => {
        ws.once("open", () => resolve());
        ws.once("error", reject);
      });
      expect(ws.readyState).toBe(WebSocket.OPEN);

      ws.close();
      await server.stop();
    });

    it("accepts WS connect when a legacy token query param is present", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );
      const address = await server.start();

      const ws = new WebSocket(
        `ws://localhost:${address.port}/ws?token=legacy`,
      );
      await new Promise<void>((resolve, reject) => {
        ws.once("open", () => resolve());
        ws.once("error", reject);
      });
      expect(ws.readyState).toBe(WebSocket.OPEN);

      ws.close();
      await server.stop();
    });
  });

  describe("client tracking", () => {
    it("reflects WebSocket clients as they connect and disconnect", async () => {
      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );
      const address = await server.start();

      expect(server.getClientCount()).toBe(0);
      expect(server.getClients()).toEqual([]);

      const ws = new WebSocket(`ws://localhost:${address.port}/ws`);
      await new Promise<void>((resolve, reject) => {
        ws.once("open", () => resolve());
        ws.once("error", reject);
      });
      await waitForAsyncWork();

      const clients = server.getClients();
      expect(server.getClientCount()).toBe(1);
      expect(clients).toHaveLength(1);
      expect(clients[0].id).toBeTruthy();
      expect(clients[0].connectedAt).toBeTruthy();

      ws.close();
      await waitForAsyncWork();

      expect(server.getClientCount()).toBe(0);
      expect(server.getClients()).toEqual([]);
      expect(events.map(event => event.type)).toContain("client_connect");
      expect(events.map(event => event.type)).toContain("client_disconnect");

      await server.stop();
    });

    it("emits client_connect after the client is registered", async () => {
      let server: BridgeServer;
      let clientCountAtConnect = -1;
      let connectedClientId: string | undefined;

      const localHandlers: WsConnectionHandler[] = [];
      const localFactory = createMockHandlerFactory(localHandlers);
      server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        localFactory,
        eventBus,
        event => {
          events.push(event);
          if (event.type === "client_connect") {
            clientCountAtConnect = server.getClientCount();
            connectedClientId = server.getClients()[0]?.id;
          }
        },
      );
      const address = await server.start();

      const ws = new WebSocket(`ws://localhost:${address.port}/ws`);
      await new Promise<void>((resolve, reject) => {
        ws.once("open", () => resolve());
        ws.once("error", reject);
      });
      await waitForAsyncWork();

      const connectEvent = events.find(
        event => event.type === "client_connect",
      );
      expect(connectEvent).toBeTruthy();
      expect(clientCountAtConnect).toBe(1);
      expect(connectedClientId).toBe(
        (connectEvent as Extract<BridgeEvent, { type: "client_connect" }>)
          .client.id,
      );

      ws.close();
      await waitForAsyncWork();
      await server.stop();
    });
  });

  describe("staticDir serving", () => {
    it("serves files from staticDir instead of placeholder", async () => {
      const { mkdtempSync, writeFileSync, rmSync } = await import("node:fs");
      const { join } = await import("node:path");
      const { tmpdir } = await import("node:os");

      const tmpDir = mkdtempSync(join(tmpdir(), "bridge-static-test-"));
      writeFileSync(join(tmpDir, "index.html"), "<h1>Real Bundle</h1>");
      writeFileSync(join(tmpDir, "app.js"), 'console.log("app");');

      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0, staticDir: tmpDir },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );
      const address = await server.start();

      try {
        // Root should serve the real index.html
        const indexResponse = await requestText(
          `http://localhost:${address.port}/?token=legacy`,
        );
        expect(indexResponse.status).toBe(200);
        expect(indexResponse.body).toContain("<h1>Real Bundle</h1>");
        expect(indexResponse.body).not.toContain("Pi Web Bridge");

        // JS asset should be served
        const jsResponse = await requestText(
          `http://localhost:${address.port}/app.js`,
        );
        expect(jsResponse.status).toBe(200);
        expect(jsResponse.body).toContain('console.log("app");');

        // Unknown path should fall back to index.html (SPA routing)
        const spaResponse = await requestText(
          `http://localhost:${address.port}/some-route`,
        );
        expect(spaResponse.status).toBe(200);
        expect(spaResponse.body).toContain("<h1>Real Bundle</h1>");
      } finally {
        await server.stop();
        rmSync(tmpDir, { recursive: true, force: true });
      }
    });

    it("injects runtime config into served html", async () => {
      const { mkdtempSync, writeFileSync, rmSync } = await import("node:fs");
      const { join } = await import("node:path");
      const { tmpdir } = await import("node:os");

      const previousDebugEnv = process.env.PI_WEB_DEBUG;
      process.env.PI_WEB_DEBUG = "1";

      const tmpDir = mkdtempSync(join(tmpdir(), "bridge-runtime-config-test-"));
      writeFileSync(
        join(tmpDir, "index.html"),
        "<html><head></head><body>Real Bundle</body></html>",
      );

      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0, staticDir: tmpDir },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );
      const address = await server.start();

      try {
        const response = await requestText(`http://localhost:${address.port}/`);
        expect(response.status).toBe(200);
        expect(response.body).toContain(
          'window.__PI_WEB_CONFIG__={"debugModeAvailable":true}',
        );
      } finally {
        await server.stop();
        rmSync(tmpDir, { recursive: true, force: true });
        if (previousDebugEnv === undefined) {
          delete process.env.PI_WEB_DEBUG;
        } else {
          process.env.PI_WEB_DEBUG = previousDebugEnv;
        }
      }
    });

    it("rejects directory traversal attempts against staticDir", async () => {
      const { mkdtempSync, writeFileSync, rmSync, mkdirSync } =
        await import("node:fs");
      const { join } = await import("node:path");
      const { tmpdir } = await import("node:os");

      const tmpDir = mkdtempSync(join(tmpdir(), "bridge-traversal-test-"));
      const secretDir = join(tmpDir, "secret");
      mkdirSync(secretDir);
      writeFileSync(join(secretDir, "key.txt"), "secret-key");
      writeFileSync(join(tmpDir, "index.html"), "<h1>Safe</h1>");

      const server = new BridgeServer(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0, staticDir: tmpDir },
        handlerFactory,
        eventBus,
        event => events.push(event),
      );
      const address = await server.start();

      try {
        // The server normalizes paths, so /../../../etc/passwd becomes /etc/passwd
        // which doesn't start with staticDir — that should 404/fallback
        const traversalResponse = await requestText(
          `http://localhost:${address.port}/../../../etc/passwd`,
        );
        // Path is normalized and doesn't match staticDir prefix → fallback to index.html (SPA)
        expect(traversalResponse.status).toBe(200);
        expect(traversalResponse.body).toContain("<h1>Safe</h1>");
        expect(traversalResponse.body).not.toContain("secret-key");

        // The secret file within staticDir should be accessible (it's a real file)
        // but URLs that resolve outside staticDir should not expose anything
        const insideResponse = await requestText(
          `http://localhost:${address.port}/secret/key.txt`,
        );
        expect(insideResponse.status).toBe(200);
        expect(insideResponse.body).toContain("secret-key");
      } finally {
        await server.stop();
        rmSync(tmpDir, { recursive: true, force: true });
      }
    });
  });
});
