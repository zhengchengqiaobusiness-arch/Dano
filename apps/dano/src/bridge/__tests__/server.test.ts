import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";
import { createHash, createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BridgeEventBus } from "../bridge-event-bus.js";
import {
  BridgeServer,
  type RpcConnectionHandler,
  type RpcConnectionHandlerFactory,
} from "../server.js";
import { createJwtUserContextResolver } from "../user-context.js";
import {
  DEFAULT_BRIDGE_CONFIG,
  type BridgeConfig,
  type ClientMessage,
  type ServerMessage,
} from "../types.js";

interface SseProbe {
  close(): void;
  waitForClose(timeoutMs?: number): Promise<void>;
  waitForMessages(count: number): Promise<ServerMessage[]>;
}

const servers: BridgeServer[] = [];
const uploadRoots: string[] = [];
const workspaceRoots: string[] = [];
const userRoots: string[] = [];

afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => server.stop()));
  for (const root of uploadRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
  for (const root of workspaceRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
  for (const root of userRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

function createServer(
  factory?: (ctx: Parameters<RpcConnectionHandlerFactory>[0]) => RpcConnectionHandler,
  config: Partial<BridgeConfig> = {},
  auth?: { secret?: string; issuer?: string; audience?: string },
) {
  const uploadDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-server-upload-"));
  const workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-workspace-"));
  uploadRoots.push(uploadDir);
  workspaceRoots.push(workspaceDir);
  const runtimeRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dano-user-runtime-"));
  userRoots.push(runtimeRoot);
  const eventBus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
  const emitEvent = vi.fn();
  const handlerFactory: RpcConnectionHandlerFactory = ctx =>
    factory?.(ctx) ?? {
      handleClientMessage: vi.fn(),
      currentGitCwd: () => workspaceDir,
      dispose: vi.fn(),
    };
  const userContextResolver = auth?.secret
    ? createJwtUserContextResolver({
        runtimeRootPath: runtimeRoot,
        secret: auth.secret,
        issuer: auth.issuer,
        audience: auth.audience,
      })
    : undefined;
  const server = new BridgeServer(
    {
      ...DEFAULT_BRIDGE_CONFIG,
      ...config,
      host: "127.0.0.1",
      port: 0,
      upload: { ...DEFAULT_BRIDGE_CONFIG.upload, ...config.upload, uploadDir },
    },
    handlerFactory,
    eventBus,
    emitEvent,
    userContextResolver,
  );
  servers.push(server);
  return { server, eventBus, emitEvent, workspaceDir, runtimeRoot };
}

const TEST_JWT_SECRET = "test-secret-that-is-long-enough";

function signJwt(
  claims: Record<string, unknown>,
  secret = TEST_JWT_SECRET,
): string {
  const encode = (value: unknown) =>
    Buffer.from(JSON.stringify(value)).toString("base64url");
  const unsigned = `${encode({ alg: "HS256", typ: "JWT" })}.${encode(claims)}`;
  const signature = createHmac("sha256", secret)
    .update(unsigned)
    .digest("base64url");
  return `${unsigned}.${signature}`;
}

function bearer(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
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
  const closeWaiters: Array<() => void> = [];
  let closed = false;
  let buffer = "";

  const req = http.get(url, res => {
    res.setEncoding("utf8");
    res.on("close", () => {
      closed = true;
      for (const resolve of closeWaiters.splice(0)) resolve();
    });
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
    waitForClose(timeoutMs = 500) {
      if (closed) return Promise.resolve();
      return new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          const idx = closeWaiters.indexOf(done);
          if (idx !== -1) closeWaiters.splice(idx, 1);
          reject(new Error("SSE stream did not close"));
        }, timeoutMs);
        const done = () => {
          clearTimeout(timer);
          resolve();
        };
        closeWaiters.push(done);
      });
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

  it("binds a verified JWT User to the client and creates that User Folder", async () => {
    let connectionUser: Parameters<RpcConnectionHandlerFactory>[0]["user"];
    const { server, runtimeRoot } = createServer(
      ctx => {
        connectionUser = ctx.user;
        return {
          handleClientMessage: vi.fn(),
          dispose: vi.fn(),
        };
      },
      {},
      { secret: TEST_JWT_SECRET, issuer: "dano-auth", audience: "dano" },
    );
    const address = await server.start();
    const token = signJwt({
      sub: "user-42",
      name: "Joseph",
      picture: "https://example.test/avatar.png",
      iss: "dano-auth",
      aud: "dano",
      exp: Math.floor(Date.now() / 1000) + 60,
    });

    const response = await fetch(
      `http://127.0.0.1:${address.port}/api/clients`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...bearer(token) },
        body: "{}",
      },
    );

    expect(response.status).toBe(201);
    await expect(response.json()).resolves.toMatchObject({
      currentUser: {
        username: "Joseph",
        avatarUrl: "https://example.test/avatar.png",
      },
    });
    expect(connectionUser).toMatchObject({
      user: { id: "user-42", username: "Joseph" },
      folderPath: fs.realpathSync(path.join(runtimeRoot, "users", "user-42")),
    });
    expect(fs.statSync(path.join(runtimeRoot, "users", "user-42")).isDirectory()).toBe(
      true,
    );
  });

  it("does not turn a client-reported identity into a User Context", async () => {
    let connectionUser: Parameters<RpcConnectionHandlerFactory>[0]["user"];
    const { server, runtimeRoot } = createServer(ctx => {
      connectionUser = ctx.user;
      return { handleClientMessage: vi.fn(), dispose: vi.fn() };
    });
    const address = await server.start();

    const response = await fetch(
      `http://127.0.0.1:${address.port}/api/clients?userId=victim`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": "victim" },
        body: JSON.stringify({ userId: "victim", username: "Victim" }),
      },
    );
    const body = (await response.json()) as Record<string, unknown>;

    expect(response.status).toBe(201);
    expect(body).not.toHaveProperty("currentUser");
    expect(connectionUser).toBeUndefined();
    expect(fs.existsSync(path.join(runtimeRoot, "users", "victim"))).toBe(false);
  });

  it("rejects an invalid JWT instead of downgrading it to an unauthenticated client", async () => {
    const { server } = createServer(undefined, {}, { secret: TEST_JWT_SECRET });
    const address = await server.start();

    const response = await fetch(`http://127.0.0.1:${address.port}/api/clients`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...bearer(signJwt({ sub: "attacker", exp: Date.now() / 1000 + 60 }, "wrong-secret")),
      },
      body: "{}",
    });

    expect(response.status).toBe(401);
    expect(server.getClientCount()).toBe(0);
  });

  it("rejects a missing token when trusted JWT authentication is configured", async () => {
    const { server, runtimeRoot } = createServer(
      undefined,
      {},
      { secret: TEST_JWT_SECRET },
    );
    const address = await server.start();

    const response = await fetch(`http://127.0.0.1:${address.port}/api/clients`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });

    expect(response.status).toBe(401);
    expect(server.getClientCount()).toBe(0);
    expect(fs.existsSync(path.join(runtimeRoot, "users"))).toBe(false);
  });

  it("rejects JWT subjects that could escape the users directory", async () => {
    const { server, runtimeRoot } = createServer(
      undefined,
      {},
      { secret: TEST_JWT_SECRET },
    );
    const address = await server.start();
    const token = signJwt({ sub: "../victim", exp: Date.now() / 1000 + 60 });

    const response = await fetch(`http://127.0.0.1:${address.port}/api/clients`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearer(token) },
      body: "{}",
    });

    expect(response.status).toBe(401);
    expect(fs.existsSync(path.join(runtimeRoot, "victim"))).toBe(false);
  });

  it("keeps different authenticated Users in separate summaries and User Folders", async () => {
    const contexts: NonNullable<Parameters<RpcConnectionHandlerFactory>[0]["user"]>[] = [];
    const { server, runtimeRoot } = createServer(
      ctx => {
        if (ctx.user) contexts.push(ctx.user);
        return { handleClientMessage: vi.fn(), dispose: vi.fn() };
      },
      {},
      { secret: TEST_JWT_SECRET },
    );
    const address = await server.start();
    const create = (sub: string, name: string) =>
      fetch(`http://127.0.0.1:${address.port}/api/clients`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...bearer(signJwt({ sub, name, exp: Date.now() / 1000 + 60 })),
        },
        body: "{}",
      });

    const [aliceResponse, bobResponse] = await Promise.all([
      create("alice", "Alice"),
      create("bob", "Bob"),
    ]);

    await expect(aliceResponse.json()).resolves.toMatchObject({
      currentUser: { username: "Alice" },
    });
    await expect(bobResponse.json()).resolves.toMatchObject({
      currentUser: { username: "Bob" },
    });
    expect(contexts.map(context => context.user.id).sort()).toEqual(["alice", "bob"]);
    expect(contexts[0]?.folderPath).not.toBe(contexts[1]?.folderPath);
    expect(fs.statSync(path.join(runtimeRoot, "users", "alice")).isDirectory()).toBe(true);
    expect(fs.statSync(path.join(runtimeRoot, "users", "bob")).isDirectory()).toBe(true);
  });

  it("keeps the same authenticated User across separate browser clients", async () => {
    const contexts: NonNullable<Parameters<RpcConnectionHandlerFactory>[0]["user"]>[] = [];
    const { server } = createServer(
      ctx => {
        if (ctx.user) contexts.push(ctx.user);
        return {
          handleClientMessage: vi.fn(),
          currentGitCwd: () => `/tmp/workspace-${contexts.length}`,
          dispose: vi.fn(),
        };
      },
      {},
      { secret: TEST_JWT_SECRET },
    );
    const address = await server.start();
    const token = signJwt({ sub: "stable-user", name: "Stable", exp: Date.now() / 1000 + 60 });
    const create = () =>
      fetch(`http://127.0.0.1:${address.port}/api/clients`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...bearer(token) },
        body: "{}",
      });

    const [first, second] = await Promise.all([create(), create()]);

    expect(first.status).toBe(201);
    expect(second.status).toBe(201);
    expect(contexts).toHaveLength(2);
    expect(contexts[0]?.user.id).toBe("stable-user");
    expect(contexts[1]?.user.id).toBe("stable-user");
    expect(contexts[0]?.folderPath).toBe(contexts[1]?.folderPath);
  });

  it("accepts a verified JWT from the HttpOnly-cookie transport used by EventSource", async () => {
    const { server } = createServer(undefined, {}, { secret: TEST_JWT_SECRET });
    const address = await server.start();
    const token = signJwt({ sub: "cookie-user", name: "Cookie User", exp: Date.now() / 1000 + 60 });

    const response = await fetch(`http://127.0.0.1:${address.port}/api/clients`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Cookie: `dano_auth=${token}` },
      body: "{}",
    });

    expect(response.status).toBe(201);
    await expect(response.json()).resolves.toMatchObject({
      currentUser: { username: "Cookie User" },
    });
  });

  it("returns the bound User summary only to the same verified User", async () => {
    const { server } = createServer(undefined, {}, { secret: TEST_JWT_SECRET });
    const address = await server.start();
    const ownerToken = signJwt({ sub: "owner", name: "Owner", exp: Date.now() / 1000 + 60 });
    const attackerToken = signJwt({ sub: "attacker", name: "Attacker", exp: Date.now() / 1000 + 60 });
    const createdResponse = await fetch(
      `http://127.0.0.1:${address.port}/api/clients`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...bearer(ownerToken) },
        body: JSON.stringify({ userId: "attacker" }),
      },
    );
    const created = (await createdResponse.json()) as { client: { id: string } };
    const attackerCreatedResponse = await fetch(
      `http://127.0.0.1:${address.port}/api/clients`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...bearer(attackerToken) },
        body: "{}",
      },
    );
    const attackerCreated = (await attackerCreatedResponse.json()) as {
      client: { id: string };
    };
    const endpoint = `http://127.0.0.1:${address.port}/api/clients/${created.client.id}/user`;
    const uploadBody = new TextEncoder().encode("owner data");
    const uploadResponse = await fetch(
      `http://127.0.0.1:${address.port}/api/uploads?clientId=${created.client.id}&name=owner.txt&mimeType=text/plain&sha256=${sha256(uploadBody)}`,
      {
        method: "POST",
        headers: bearer(ownerToken),
        body: uploadBody,
      },
    );
    const upload = (await uploadResponse.json()) as { id: string };

    const ownerResponse = await fetch(endpoint, { headers: bearer(ownerToken) });
    const missingResponse = await fetch(endpoint);
    const attackerResponse = await fetch(endpoint, { headers: bearer(attackerToken) });
    const attackerMessageResponse = await fetch(
      `http://127.0.0.1:${address.port}/api/clients/${created.client.id}/messages`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...bearer(attackerToken) },
        body: JSON.stringify({ type: "command", payload: { id: "attack" } }),
      },
    );
    const attackerUploadResponse = await fetch(
      `http://127.0.0.1:${address.port}/api/uploads?clientId=${created.client.id}&name=attack.txt&mimeType=text/plain&sha256=${sha256(uploadBody)}`,
      { method: "POST", headers: bearer(attackerToken), body: uploadBody },
    );
    const attackerLookupResponse = await fetch(
      `http://127.0.0.1:${address.port}/api/uploads/lookup?clientId=${created.client.id}&name=owner.txt&mimeType=text/plain&sha256=${sha256(uploadBody)}`,
      { headers: bearer(attackerToken) },
    );
    const attackerWorkspaceResponse = await fetch(
      `http://127.0.0.1:${address.port}/api/workspace-files/preview?clientId=${created.client.id}&path=owner.txt`,
      { headers: bearer(attackerToken) },
    );
    const attackerPreviewResponse = await fetch(
      `http://127.0.0.1:${address.port}/api/uploads/${upload.id}/preview?clientId=${attackerCreated.client.id}`,
      { headers: bearer(attackerToken) },
    );
    const attackerOrphanResponse = await fetch(
      `http://127.0.0.1:${address.port}/api/uploads/${upload.id}/orphan?clientId=${created.client.id}`,
      { method: "POST", headers: bearer(attackerToken), body: "{}" },
    );

    expect(uploadResponse.status).toBe(201);
    expect(ownerResponse.status).toBe(200);
    await expect(ownerResponse.json()).resolves.toEqual({ username: "Owner" });
    expect(missingResponse.status).toBe(401);
    expect(attackerResponse.status).toBe(403);
    expect(attackerMessageResponse.status).toBe(403);
    expect(attackerUploadResponse.status).toBe(403);
    expect(attackerLookupResponse.status).toBe(403);
    expect(attackerWorkspaceResponse.status).toBe(403);
    expect(attackerPreviewResponse.status).toBe(403);
    expect(attackerOrphanResponse.status).toBe(403);
  });

  it("rejects an existing symlink at the mapped User Folder boundary", async () => {
    const { server, runtimeRoot } = createServer(
      undefined,
      {},
      { secret: TEST_JWT_SECRET },
    );
    const outside = fs.mkdtempSync(path.join(os.tmpdir(), "dano-user-outside-"));
    userRoots.push(outside);
    fs.mkdirSync(path.join(runtimeRoot, "users"), { recursive: true });
    fs.symlinkSync(outside, path.join(runtimeRoot, "users", "linked-user"));
    const address = await server.start();
    const token = signJwt({ sub: "linked-user", exp: Date.now() / 1000 + 60 });

    const response = await fetch(`http://127.0.0.1:${address.port}/api/clients`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearer(token) },
      body: "{}",
    });

    expect(response.status).toBe(403);
    expect(server.getClientCount()).toBe(0);
  });

  it("rejects a symlink used as the users root", async () => {
    const { server, runtimeRoot } = createServer(
      undefined,
      {},
      { secret: TEST_JWT_SECRET },
    );
    const outside = fs.mkdtempSync(path.join(os.tmpdir(), "dano-users-root-outside-"));
    userRoots.push(outside);
    fs.symlinkSync(outside, path.join(runtimeRoot, "users"));
    const address = await server.start();
    const token = signJwt({ sub: "escaped-user", exp: Date.now() / 1000 + 60 });

    const response = await fetch(`http://127.0.0.1:${address.port}/api/clients`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearer(token) },
      body: "{}",
    });

    expect(response.status).toBe(403);
    expect(fs.existsSync(path.join(outside, "escaped-user"))).toBe(false);
    expect(server.getClientCount()).toBe(0);
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

  it("returns 202 before an asynchronous provider-stage prompt error is presented", async () => {
    let presentProviderError!: () => void;
    const providerErrorGate = new Promise<void>(resolve => {
      presentProviderError = resolve;
    });
    const { server, eventBus } = createServer(ctx => ({
      handleClientMessage(message: ClientMessage) {
        if (message.type !== "command" || message.payload.type !== "prompt") {
          return;
        }
        // The real 429 and JSONL ordering are covered by llm-provider-timeout;
        // this transport seam proves HTTP acceptance does not await that stage.
        void providerErrorGate.then(() => {
          ctx.send({
            type: "event",
            payload: {
              type: "command_error",
              commandType: "prompt",
              error: "provider rate limit",
            },
          });
        });
      },
      dispose: vi.fn(),
    }));
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{
      client: { id: string };
      eventsUrl: string;
      messagesUrl: string;
    }>(`${origin}/api/clients`);
    const sse = openSse(`${origin}${created.eventsUrl}`);
    await vi.waitFor(() =>
      expect(eventBus.hasActiveClientConnection(created.client.id)).toBe(true),
    );

    const response = await fetch(`${origin}${created.messagesUrl}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "command",
        payload: {
          id: "provider-rate-limit",
          type: "prompt",
          message: "accepted before provider execution",
        },
      } satisfies ClientMessage),
    });

    expect(response.status).toBe(202);
    await expect(response.json()).resolves.toEqual({ status: "accepted" });
    presentProviderError();
    await expect(sse.waitForMessages(1)).resolves.toMatchObject([
      {
        type: "event",
        payload: {
          type: "command_error",
          commandType: "prompt",
          error: "provider rate limit",
        },
      },
    ]);
    sse.close();
  });

  it("rejects client messages when no SSE stream is active", async () => {
    const commandSpy = vi.fn();
    const { server } = createServer(() => ({
      handleClientMessage: commandSpy,
      dispose: vi.fn(),
    }));
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ messagesUrl: string }>(`${origin}/api/clients`);

    const response = await fetch(`${origin}${created.messagesUrl}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "command",
        payload: { id: "cmd-1", type: "get_state" },
      } satisfies ClientMessage),
    });

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual({ error: "RECONNECT_REQUIRED" });
    expect(commandSpy).not.toHaveBeenCalled();
  });

  it("sends observable heartbeat events over SSE", async () => {
    const { server } = createServer(undefined, { heartbeatInterval: 10 });
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ eventsUrl: string }>(`${origin}/api/clients`);
    const sse = openSse(`${origin}${created.eventsUrl}`);

    const [message] = await sse.waitForMessages(1);
    sse.close();
    expect(message).toMatchObject({
      type: "event",
      payload: {
        type: "heartbeat",
        serverInstanceId: expect.any(String),
        serverStartTime: expect.any(String),
      },
    });
  });

  it("keeps POST delivery valid after a heartbeat for the same client", async () => {
    const commandSpy = vi.fn();
    const { server } = createServer(() => ({
      handleClientMessage: commandSpy,
      dispose: vi.fn(),
    }), { heartbeatInterval: 10 });
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ eventsUrl: string; messagesUrl: string }>(
      `${origin}/api/clients`,
    );
    const sse = openSse(`${origin}${created.eventsUrl}`);

    await sse.waitForMessages(1);
    await postJson(`${origin}${created.messagesUrl}`, {
      type: "command",
      payload: { id: "cmd-after-heartbeat", type: "get_state" },
    });

    sse.close();
    expect(commandSpy).toHaveBeenCalledWith({
      type: "command",
      payload: { id: "cmd-after-heartbeat", type: "get_state" },
    });
  });

  it("replaces an existing SSE stream for the same logical client", async () => {
    const commandSpy = vi.fn();
    const dispose = vi.fn();
    const { server } = createServer(() => ({
      handleClientMessage: commandSpy,
      dispose,
    }), { heartbeatInterval: 10 });
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ eventsUrl: string; messagesUrl: string }>(
      `${origin}/api/clients`,
    );
    const firstSse = openSse(`${origin}${created.eventsUrl}`);

    await firstSse.waitForMessages(1);
    const secondSse = openSse(`${origin}${created.eventsUrl}`);

    await expect(firstSse.waitForClose()).resolves.toBeUndefined();
    await secondSse.waitForMessages(1);
    await postJson(`${origin}${created.messagesUrl}`, {
      type: "command",
      payload: { id: "cmd-after-replace", type: "get_state" },
    });

    secondSse.close();
    expect(commandSpy).toHaveBeenCalledWith({
      type: "command",
      payload: { id: "cmd-after-replace", type: "get_state" },
    });
    expect(dispose).not.toHaveBeenCalled();
  });

  it("closes the SSE stream when the logical client is unregistered", async () => {
    const { server } = createServer(undefined, { heartbeatInterval: 10 });
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ client: { id: string }; eventsUrl: string }>(
      `${origin}/api/clients`,
    );
    const sse = openSse(`${origin}${created.eventsUrl}`);

    await sse.waitForMessages(1);
    await postJson(`${origin}/api/clients/${created.client.id}/disconnect`);

    await expect(sse.waitForClose()).resolves.toBeUndefined();
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
        slashCommandsAndMentionsEnabled: true,
        transcriptProcessSummaryEnabled: true,
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
    expect(spaHtml).toContain('"slashCommandsAndMentionsEnabled":true');
    expect(spaHtml).toContain('"transcriptProcessSummaryEnabled":true');
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

  it("rejects uploads when the workspace uploads directory resolves outside the workspace", async () => {
    const { server, workspaceDir } = createServer();
    const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-upload-outside-"));
    uploadRoots.push(outsideDir);
    fs.symlinkSync(outsideDir, path.join(workspaceDir, "uploads"), "dir");
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ client: { id: string } }>(
      `${origin}/api/clients`,
    );
    const body = new TextEncoder().encode("must stay inside workspace");

    const response = await postBytes(
      `${origin}/api/uploads?clientId=${encodeURIComponent(created.client.id)}&name=notes.txt&mimeType=text/plain&sha256=${sha256(body)}`,
      body,
      { "Content-Type": "text/plain" },
    );

    expect(response.status).toBe(403);
    expect(fs.readdirSync(outsideDir)).toEqual([]);
  });

  it("rejects workspace file previews when a symlink resolves outside the workspace", async () => {
    const { server, workspaceDir } = createServer();
    const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-preview-outside-"));
    uploadRoots.push(outsideDir);
    fs.writeFileSync(path.join(outsideDir, "secret.txt"), "outside");
    fs.symlinkSync(path.join(outsideDir, "secret.txt"), path.join(workspaceDir, "link.txt"));
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ client: { id: string } }>(
      `${origin}/api/clients`,
    );

    const response = await fetch(
      `${origin}/api/workspace-files/preview?clientId=${encodeURIComponent(created.client.id)}&path=link.txt`,
    );

    expect(response.status).toBe(403);
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

  it("computes the storage hash when uploads omit a declared sha256", async () => {
    const { server, workspaceDir } = createServer();
    const address = await server.start();
    const origin = `http://127.0.0.1:${address.port}`;
    const created = await postJson<{ client: { id: string } }>(
      `${origin}/api/clients`,
    );
    const body = new TextEncoder().encode("hash me on the server");
    const hash = sha256(body);

    const response = await postBytes(
      `${origin}/api/uploads?clientId=${encodeURIComponent(created.client.id)}&name=http-only.txt&mimeType=text/plain`,
      body,
      { "Content-Type": "text/plain" },
    );

    expect(response.status).toBe(201);
    const uploaded = (await response.json()) as { previewUrl: string };
    expect(uploaded).toMatchObject({
      name: "http-only.txt",
      path: path.join(workspaceDir, "uploads", `${hash}.txt`),
      relativePath: `uploads/${hash}.txt`,
    });
    const preview = await fetch(`${origin}${uploaded.previewUrl}`);
    expect(preview.status).toBe(200);
    expect(await preview.text()).toBe("hash me on the server");
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

  it("rejects uploads with an invalid declared sha256", async () => {
    const { server } = createServer();
    const address = await server.start();
    const created = await postJson<{ client: { id: string } }>(
      `http://127.0.0.1:${address.port}/api/clients`,
    );

    const response = await postBytes(
      `http://127.0.0.1:${address.port}/api/uploads?clientId=${encodeURIComponent(created.client.id)}&name=note.txt&mimeType=text/plain&sha256=not-a-hash`,
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
