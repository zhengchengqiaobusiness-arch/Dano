import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";
import { createHash } from "node:crypto";
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
const uploadRoots: string[] = [];
const workspaceRoots: string[] = [];

afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => server.stop()));
  for (const root of uploadRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
  for (const root of workspaceRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

function createServer(
  factory?: (ctx: Parameters<RpcConnectionHandlerFactory>[0]) => RpcConnectionHandler,
) {
  const uploadDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-server-upload-"));
  const workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-workspace-"));
  uploadRoots.push(uploadDir);
  workspaceRoots.push(workspaceDir);
  const eventBus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
  const emitEvent = vi.fn();
  const handlerFactory: RpcConnectionHandlerFactory = ctx =>
    factory?.(ctx) ?? {
      handleClientMessage: vi.fn(),
      currentGitCwd: () => workspaceDir,
      dispose: vi.fn(),
    };
  const server = new BridgeServer(
    {
      ...DEFAULT_BRIDGE_CONFIG,
      host: "127.0.0.1",
      port: 0,
      upload: { ...DEFAULT_BRIDGE_CONFIG.upload, uploadDir },
    },
    handlerFactory,
    eventBus,
    emitEvent,
  );
  servers.push(server);
  return { server, eventBus, emitEvent, workspaceDir };
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

async function postBytes(
  url: string,
  body: Uint8Array,
  headers: Record<string, string> = {},
): Promise<Response> {
  return fetch(url, { method: "POST", headers, body });
}

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
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

  it("uploads arbitrary files into the current workspace by declared hash", async () => {
    const { server, workspaceDir } = createServer();
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ client: { id: string } }>(
      `${origin}/api/clients`,
    );
    const body = new TextEncoder().encode("hello backend storage");
    const hash = sha256(body);

    const uploadResponse = await postBytes(
      `${origin}/api/uploads?clientId=${encodeURIComponent(created.client.id)}&name=notes.txt&mimeType=text/plain&sha256=${hash}&workspacePath=/tmp/ignored`,
      body,
      { "Content-Type": "text/plain" },
    );
    expect(uploadResponse.status).toBe(201);
    const uploaded = (await uploadResponse.json()) as {
      id: string;
      name: string;
      size: number;
      mimeType: string;
      path: string;
      relativePath: string;
      previewUrl: string;
    };

    expect(uploaded).toMatchObject({
      name: "notes.txt",
      size: body.length,
      mimeType: "text/plain",
      relativePath: `uploads/${hash}.txt`,
    });
    expect(uploaded.path).toBe(path.join(workspaceDir, "uploads", `${hash}.txt`));
    expect(fs.existsSync(uploaded.path)).toBe(true);

    const previewResponse = await fetch(`${origin}${uploaded.previewUrl}`);
    expect(previewResponse.status).toBe(200);
    expect(previewResponse.headers.get("content-type")).toBe("text/plain");
    expect(new Uint8Array(await previewResponse.arrayBuffer())).toEqual(body);

    const workspacePreview = await fetch(
      `${origin}/api/workspace-files/preview?clientId=${encodeURIComponent(created.client.id)}&path=${encodeURIComponent(uploaded.relativePath)}`,
    );
    expect(workspacePreview.status).toBe(200);
    expect(await workspacePreview.text()).toBe("hello backend storage");

    const outsidePreview = await fetch(
      `${origin}/api/workspace-files/preview?clientId=${encodeURIComponent(created.client.id)}&path=..%2Fsecret.txt`,
    );
    expect(outsidePreview.status).toBe(403);
  });

  it("accepts uploads with empty or unknown MIME by using octet-stream", async () => {
    const { server } = createServer();
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ client: { id: string } }>(
      `${origin}/api/clients`,
    );
    const body = new Uint8Array([1, 2, 3]);

    const response = await postBytes(
      `${origin}/api/uploads?clientId=${encodeURIComponent(created.client.id)}&name=blob&mimeType=&sha256=${sha256(body)}`,
      body,
    );

    expect(response.status).toBe(201);
    await expect(response.json()).resolves.toMatchObject({
      name: "blob",
      mimeType: "application/octet-stream",
    });
  });

  it("rejects uploads without a valid client id", async () => {
    const { server } = createServer();
    const address = await server.start();

    const response = await postBytes(
      `http://127.0.0.1:${address.port}/api/uploads?name=sample.png&mimeType=image/png&sha256=${sha256(new Uint8Array([1]))}`,
      new Uint8Array([1]),
      { "Content-Type": "image/png" },
    );

    expect(response.status).toBe(400);
  });

  it("marks uploaded drafts orphaned only for their owner client", async () => {
    const { server } = createServer();
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const owner = await postJson<{ client: { id: string } }>(
      `${origin}/api/clients`,
    );
    const other = await postJson<{ client: { id: string } }>(
      `${origin}/api/clients`,
    );
    const uploadResponse = await postBytes(
      `${origin}/api/uploads?clientId=${encodeURIComponent(owner.client.id)}&name=sample.png&mimeType=image/png&sha256=${sha256(new Uint8Array([1]))}`,
      new Uint8Array([1]),
      { "Content-Type": "image/png" },
    );
    expect(uploadResponse.status).toBe(201);
    const uploaded = (await uploadResponse.json()) as { id: string };

    const forbidden = await fetch(
      `${origin}/api/uploads/${encodeURIComponent(uploaded.id)}/orphan?clientId=${encodeURIComponent(other.client.id)}`,
      { method: "POST", body: "{}" },
    );
    expect(forbidden.status).toBe(403);

    const orphaned = await fetch(
      `${origin}/api/uploads/${encodeURIComponent(uploaded.id)}/orphan?clientId=${encodeURIComponent(owner.client.id)}`,
      { method: "POST", body: "{}" },
    );
    expect(orphaned.status).toBe(202);
  });

  it("rejects uploads without a file name", async () => {
    const { server } = createServer();
    const address = await server.start();
    const created = await postJson<{ client: { id: string } }>(
      `http://127.0.0.1:${address.port}/api/clients`,
    );

    const response = await postBytes(
      `http://127.0.0.1:${address.port}/api/uploads?clientId=${encodeURIComponent(created.client.id)}&mimeType=image/png&sha256=${sha256(new Uint8Array([1]))}`,
      new Uint8Array([1]),
      { "Content-Type": "image/png" },
    );

    expect(response.status).toBe(400);
  });

  it("rejects uploads without a declared sha256", async () => {
    const { server } = createServer();
    const address = await server.start();
    const created = await postJson<{ client: { id: string } }>(
      `http://127.0.0.1:${address.port}/api/clients`,
    );

    const response = await postBytes(
      `http://127.0.0.1:${address.port}/api/uploads?clientId=${encodeURIComponent(created.client.id)}&name=note.txt&mimeType=text/plain`,
      new Uint8Array([1]),
      { "Content-Type": "text/plain" },
    );

    expect(response.status).toBe(400);
  });

  it("rejects hash mismatches and removes the partial file", async () => {
    const { server, workspaceDir } = createServer();
    const address = await server.start();
    const created = await postJson<{ client: { id: string } }>(
      `http://127.0.0.1:${address.port}/api/clients`,
    );
    const body = new TextEncoder().encode("real content");
    const wrongHash = "0".repeat(64);

    const response = await postBytes(
      `http://127.0.0.1:${address.port}/api/uploads?clientId=${encodeURIComponent(created.client.id)}&name=note.md&mimeType=text/markdown&sha256=${wrongHash}`,
      body,
      { "Content-Type": "text/markdown" },
    );

    expect(response.status).toBe(400);
    expect(fs.readdirSync(path.join(workspaceDir, "uploads"))).toEqual([]);
  });

  it("dedupes storage by hash while keeping each ref owner and original name", async () => {
    const { server } = createServer();
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const owner = await postJson<{ client: { id: string } }>(
      `${origin}/api/clients`,
    );
    const other = await postJson<{ client: { id: string } }>(
      `${origin}/api/clients`,
    );
    const body = new TextEncoder().encode("same bytes");
    const hash = sha256(body);

    const first = await postBytes(
      `${origin}/api/uploads?clientId=${encodeURIComponent(owner.client.id)}&name=first.txt&mimeType=text/plain&sha256=${hash}`,
      body,
      { "Content-Type": "text/plain" },
    ).then(response => response.json() as Promise<{ path: string }>);
    const second = await postBytes(
      `${origin}/api/uploads?clientId=${encodeURIComponent(other.client.id)}&name=second.txt&mimeType=text/plain&sha256=${hash}`,
      body,
      { "Content-Type": "text/plain" },
    ).then(response => response.json() as Promise<{ id: string; name: string; path: string }>);

    expect(second).toMatchObject({ name: "second.txt", path: first.path });

    await fetch(
      `${origin}/api/uploads/${encodeURIComponent(second.id)}/orphan?clientId=${encodeURIComponent(other.client.id)}`,
      { method: "POST", body: "{}" },
    );
    expect(fs.existsSync(first.path)).toBe(true);
  });

  it("returns an existing upload by hash lookup without reading a request body", async () => {
    const { server } = createServer();
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ client: { id: string } }>(
      `${origin}/api/clients`,
    );
    const body = new TextEncoder().encode("lookup bytes");
    const hash = sha256(body);
    const uploaded = await postBytes(
      `${origin}/api/uploads?clientId=${encodeURIComponent(created.client.id)}&name=first.pdf&mimeType=application/pdf&sha256=${hash}`,
      body,
      { "Content-Type": "application/pdf" },
    ).then(
      response =>
        response.json() as Promise<{ path: string; relativePath: string }>,
    );

    const lookup = await fetch(
      `${origin}/api/uploads/lookup?clientId=${encodeURIComponent(created.client.id)}&name=second.pdf&mimeType=application/pdf&sha256=${hash}`,
    );

    expect(lookup.status).toBe(200);
    await expect(lookup.json()).resolves.toMatchObject({
      name: "second.pdf",
      path: uploaded.path,
      relativePath: uploaded.relativePath,
    });
  });

  it("rejects image uploads over the 50 MB limit before storing them", async () => {
    const { server } = createServer();
    const address = await server.start();
    const created = await postJson<{ client: { id: string } }>(
      `http://127.0.0.1:${address.port}/api/clients`,
    );
    const response = await new Promise<http.IncomingMessage>(resolve => {
      const req = http.request(
        {
          host: "127.0.0.1",
          port: address.port,
          path: `/api/uploads?clientId=${encodeURIComponent(created.client.id)}&name=large.png&mimeType=image/png&sha256=${"0".repeat(64)}`,
          method: "POST",
          headers: {
            "Content-Type": "image/png",
            "Content-Length": String(50 * 1024 * 1024 + 1),
          },
        },
        resolve,
      );
      req.end();
    });

    expect(response.statusCode).toBe(413);
  });

  it("returns 404 for missing upload previews", async () => {
    const { server } = createServer();
    const address = await server.start();

    const response = await fetch(
      `http://127.0.0.1:${address.port}/api/uploads/missing/preview`,
    );

    expect(response.status).toBe(404);
  });
});
