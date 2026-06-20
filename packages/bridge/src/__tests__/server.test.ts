import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BridgeEventBus } from "../bridge-event-bus.js";
import {
  BridgeServer,
  type RpcConnectionHandler,
  type RpcConnectionHandlerFactory,
} from "../server.js";
import {
  DEFAULT_BRIDGE_CONFIG,
  type ClientMessage,
  type ServerMessage,
} from "../types.js";

interface SseProbe {
  close(): void;
  waitForMessages(count: number): Promise<ServerMessage[]>;
}

const servers: BridgeServer[] = [];

afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => server.stop()));
});

function createServer(
  factory?: (ctx: Parameters<RpcConnectionHandlerFactory>[0]) => RpcConnectionHandler,
) {
  const eventBus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
  const emitEvent = vi.fn();
  const handlerFactory: RpcConnectionHandlerFactory = ctx =>
    factory?.(ctx) ?? {
      handleClientMessage: vi.fn(),
      dispose: vi.fn(),
    };
  const server = new BridgeServer(
    { ...DEFAULT_BRIDGE_CONFIG, host: "127.0.0.1", port: 0 },
    handlerFactory,
    eventBus,
    emitEvent,
  );
  servers.push(server);
  return { server, eventBus, emitEvent };
}

async function postJson<T>(url: string, body: unknown = {}): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  expect(response.ok).toBe(true);
  return (await response.json()) as T;
}

async function readJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  expect(response.ok).toBe(true);
  return (await response.json()) as T;
}

function openSse(url: string): SseProbe {
  const messages: ServerMessage[] = [];
  const waiters: Array<{
    count: number;
    resolve: (messages: ServerMessage[]) => void;
  }> = [];
  let buffer = "";

  const req = http.get(url, res => {
    res.setEncoding("utf8");
    res.on("data", chunk => {
      buffer += chunk;
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame
          .split(/\r?\n/)
          .filter(line => line.startsWith("data: "))
          .map(line => line.slice("data: ".length))
          .join("\n");
        if (data) {
          messages.push(JSON.parse(data) as ServerMessage);
          for (const waiter of waiters.slice()) {
            if (messages.length >= waiter.count) {
              waiters.splice(waiters.indexOf(waiter), 1);
              waiter.resolve(messages.slice(0, waiter.count));
            }
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
    });
  });

  return {
    close() {
      req.destroy();
    },
    waitForMessages(count: number) {
      if (messages.length >= count) {
        return Promise.resolve(messages.slice(0, count));
      }
      return new Promise<ServerMessage[]>(resolve => {
        waiters.push({ count, resolve });
      });
    },
  };
}

describe("BridgeServer HTTP/SSE transport", () => {
  it("serves health over HTTP", async () => {
    const { server } = createServer();
    const address = await server.start();

    await expect(
      readJson<{ status: string }>(`http://127.0.0.1:${address.port}/api/health`),
    ).resolves.toEqual({ status: "ok" });
  });

  it("creates a logical client without opening an SSE stream first", async () => {
    const { server, emitEvent } = createServer();
    const address = await server.start();

    const created = await postJson<{
      client: { id: string; seq: number };
      eventsUrl: string;
      messagesUrl: string;
      defaultWorkspacePath?: string;
    }>(`http://127.0.0.1:${address.port}/api/clients`);

    expect(created.client.id).toMatch(/^client_/);
    expect(created.client.seq).toBe(1);
    expect(created.eventsUrl).toContain("/events");
    expect(created.messagesUrl).toContain("/messages");
    expect(created.defaultWorkspacePath).toBe("/tmp/dano");
    expect(server.getClientCount()).toBe(1);
    expect(emitEvent).toHaveBeenCalledWith(
      expect.objectContaining({ type: "client_connect" }),
    );
  });

  it("delivers command responses over SSE after POST accepts the command", async () => {
    const commandSpy = vi.fn();
    const { server } = createServer(ctx => ({
      handleClientMessage(message: ClientMessage) {
        commandSpy(message);
        ctx.send({
          type: "response",
          payload: {
            id: "cmd-1",
            type: "response",
            command: "get_state",
            success: true,
            data: {
              thinkingLevel: "medium",
              isStreaming: false,
              isCompacting: false,
              steeringMode: "all",
              followUpMode: "all",
              sessionId: "session-1",
              autoCompactionEnabled: true,
              messageCount: 0,
              pendingMessageCount: 0,
            },
          },
        });
      },
      dispose: vi.fn(),
    }));
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ eventsUrl: string; messagesUrl: string }>(
      `${origin}/api/clients`,
    );
    const sse = openSse(`${origin}${created.eventsUrl}`);

    await postJson(`${origin}${created.messagesUrl}`, {
      type: "command",
      payload: { id: "cmd-1", type: "get_state" },
    });

    const [message] = await sse.waitForMessages(1);
    sse.close();
    expect(commandSpy).toHaveBeenCalledWith({
      type: "command",
      payload: { id: "cmd-1", type: "get_state" },
    });
    expect(message).toMatchObject({
      type: "response",
      payload: { id: "cmd-1", success: true },
    });
  });

  it("buffers server messages until the SSE stream connects", async () => {
    const { server } = createServer(ctx => {
      ctx.send({
        type: "event",
        payload: { type: "session_compact" },
      });
      return {
        handleClientMessage: vi.fn(),
        dispose: vi.fn(),
      };
    });
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ eventsUrl: string }>(`${origin}/api/clients`);
    const sse = openSse(`${origin}${created.eventsUrl}`);

    const [message] = await sse.waitForMessages(1);
    sse.close();
    expect(message).toEqual({
      type: "event",
      payload: { type: "session_compact" },
    });
  });

  it("disconnects clients through the HTTP disconnect endpoint", async () => {
    const dispose = vi.fn();
    const { server, emitEvent } = createServer(ctx => ({
      handleClientMessage: vi.fn(),
      dispose: () => {
        dispose();
        ctx.emitEvent({
          type: "client_disconnect",
          client: ctx.client,
          reason: "adapter_disposed",
        });
      },
    }));
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ client: { id: string } }>(
      `${origin}/api/clients`,
    );

    await postJson(`${origin}/api/clients/${created.client.id}/disconnect`);

    expect(dispose).toHaveBeenCalledTimes(1);
    expect(server.getClientCount()).toBe(0);
    expect(emitEvent).toHaveBeenCalledWith(
      expect.objectContaining({ type: "client_disconnect" }),
    );
  });

  it("serves static assets and falls back to index.html for SPA routes", async () => {
    const staticDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-static-"));
    fs.writeFileSync(path.join(staticDir, "index.html"), "<main>index</main>");
    fs.writeFileSync(path.join(staticDir, "asset.txt"), "asset");
    const eventBus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
    const server = new BridgeServer(
      {
        ...DEFAULT_BRIDGE_CONFIG,
        host: "127.0.0.1",
        port: 0,
        staticDir,
        productName: "Custom Agent",
        emptyState: {
          mode: "html",
          content: "<strong>给 {产品名称} 发消息</strong>",
        },
        quickActions: [
          { label: "请假", prompt: "帮我申请请假" },
        ],
      },
      () => ({ handleClientMessage: vi.fn(), dispose: vi.fn() }),
      eventBus,
      vi.fn(),
    );
    servers.push(server);
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;

    await expect(fetch(`${origin}/asset.txt`).then(r => r.text())).resolves.toBe(
      "asset",
    );
    const spaHtml = await fetch(`${origin}/missing/route`).then(r => r.text());
    expect(spaHtml).toContain("<main>index</main>");
    expect(spaHtml).toContain("window.__PI_WEB_CONFIG__=");
    expect(spaHtml).toContain('"productName":"Custom Agent"');
    expect(spaHtml).toContain(
      '"emptyState":{"mode":"html","content":"\\u003cstrong>给 {产品名称} 发消息\\u003c/strong>"}',
    );
    expect(spaHtml).toContain(
      '"quickActions":[{"label":"请假","prompt":"帮我申请请假"}]',
    );
  });
});
