/** @vitest-environment happy-dom */

import type {
  BridgeAuthenticationState,
  BridgeUserSummary,
  RpcCommand,
} from "@dano/types/protocol";
import { afterEach, describe, expect, it, vi } from "vitest";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(r => {
    resolve = r;
  });
  return { promise, resolve };
}

class FakeEventSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  readyState = FakeEventSource.CONNECTING;

  constructor(readonly url: string) {
    super();
    eventSources.push(this);
  }

  open() {
    this.readyState = FakeEventSource.OPEN;
    this.dispatchEvent(new Event("open"));
  }

  send(payload: unknown) {
    this.dispatchEvent(
      new MessageEvent("message", { data: JSON.stringify(payload) }),
    );
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
}

const eventSources: FakeEventSource[] = [];

async function startBridge(fetchImpl: typeof fetch) {
  vi.stubGlobal("fetch", fetchImpl);
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.spyOn(navigator, "sendBeacon").mockReturnValue(true);

  const { initBridge } = await import("./bridgeStore.svelte");
  const bridge = initBridge();
  await vi.waitFor(() => expect(eventSources).toHaveLength(1));
  eventSources[0]!.open();
  await vi.waitFor(() => expect(bridge.connectionStatus).toBe("connected"));
  return bridge;
}

async function connectBridge(
  promptResponse: Promise<Response> | (() => Promise<Response>),
  onPromptRequest?: (init: RequestInit | undefined) => void,
  currentUser?: BridgeUserSummary,
  authentication?: BridgeAuthenticationState,
) {
  const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input) === "/api/clients") {
      return new Response(
        JSON.stringify({
          client: { id: "client-1" },
          eventsUrl: "/events",
          messagesUrl: "/messages",
          ...(currentUser ? { currentUser } : {}),
          ...(authentication ? { authentication } : {}),
        }),
        { status: 201, headers: { "content-type": "application/json" } },
      );
    }
    const envelope = JSON.parse(String(init?.body ?? "{}")) as {
      payload?: { type?: string };
    };
    if (envelope.payload?.type === "prompt") {
      onPromptRequest?.(init);
      return typeof promptResponse === "function"
        ? promptResponse()
        : promptResponse;
    }
    return new Response(null, { status: 202 });
  });
  return startBridge(fetchImpl);
}

interface InitialSessionFixture {
  id: string;
  name: string;
  path: string;
  updatedAt: string;
  content: string;
}

async function connectWithDefaultWorkspaceSessions(
  sessions: InitialSessionFixture[],
  cachedSessionPath?: string,
  newSessionCancelled = false,
) {
  const workspacePath = "/users/current/workspaces/default";
  const newSessionPath = "/users/current/sessions/new.jsonl";
  const commands: RpcCommand[] = [];
  const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    if (url === "/api/auth/current") {
      return new Response(
        JSON.stringify({
          status: "authenticated",
          user: { id: "user-alice", username: "Alice" },
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      );
    }
    if (url === "/api/clients") {
      return new Response(
        JSON.stringify({
          client: { id: "client-1" },
          eventsUrl: "/events",
          messagesUrl: "/messages",
          defaultWorkspacePath: workspacePath,
          authentication: {
            status: "authenticated",
            user: { id: "user-alice", username: "Alice" },
          },
        }),
        { status: 201, headers: { "content-type": "application/json" } },
      );
    }
    if (url === "/messages") {
      const envelope = JSON.parse(String(init?.body)) as {
        payload: RpcCommand & { id: string };
      };
      const command = envelope.payload;
      commands.push(command);
      const selectedSession =
        command.type === "switch_session"
          ? sessions.find(session => session.path === command.sessionPath)
          : undefined;
      const dataByCommand: Record<string, unknown> = {
        list_workspaces: {
          workspaces: [
            { id: workspacePath, name: "default", path: workspacePath },
          ],
        },
        get_available_models: { models: [] },
        get_commands: { commands: [] },
        register_workspace: {
          workspaceId: workspacePath,
          workspaceName: "default",
          workspacePath,
          created: false,
          cancelled: false,
        },
        new_session: {
          transcript: {
            messages: [],
            sessionPath: newSessionPath,
            hasOlder: false,
            hasNewer: false,
          },
          treeEntries: [],
          sessionId: "new-session",
          sessionName: "New session",
          sessionPath: newSessionPath,
          workspacePath,
          cancelled: newSessionCancelled,
        },
        list_sessions: {
          sessions: sessions.slice(
            0,
            command.type === "list_sessions" ? command.limit : undefined,
          ).map(({ content: _, ...session }) => ({
            ...session,
            workspacePath,
          })),
          workspacePath,
          merge: "replace",
        },
        switch_session: selectedSession
          ? {
              transcript: {
                messages: [
                  {
                    id: selectedSession.id,
                    role: "user",
                    content: selectedSession.content,
                  },
                ],
                sessionPath: selectedSession.path,
                hasOlder: false,
                hasNewer: false,
              },
              treeEntries: [],
              sessionId: selectedSession.id,
              sessionName: selectedSession.name,
              sessionPath: selectedSession.path,
              workspacePath,
            }
          : undefined,
        get_state: selectedSession
          ? {
              sessionId: selectedSession.id,
              sessionName: selectedSession.name,
              sessionFile: selectedSession.path,
              workspacePath,
              isStreaming: false,
              isCompacting: false,
              autoCompactionEnabled: false,
              messageCount: 1,
              pendingMessageCount: 0,
            }
          : undefined,
      };
      queueMicrotask(() => {
        eventSources[0]?.send({
          type: "response",
          payload: {
            id: command.id,
            type: "response",
            command: command.type,
            success: true,
            data: dataByCommand[command.type],
          },
        });
      });
      return new Response(null, { status: 202 });
    }
    return new Response(null, { status: 404 });
  });
  window.sessionStorage.clear();
  if (cachedSessionPath) {
    const { writeActiveSessionCache } = await import("../utils/newSession");
    writeActiveSessionCache(window.sessionStorage, cachedSessionPath);
  }
  const bridge = await startBridge(fetchImpl);
  return { bridge, commands, newSessionPath };
}

describe("Bridge prompt acceptance", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.resetModules();
    eventSources.length = 0;
  });

  it("projects checking before the server resolves authentication", async () => {
    const current = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async input => {
        if (String(input) === "/api/auth/current") return current.promise;
        return new Response(null, { status: 503 });
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.spyOn(navigator, "sendBeacon").mockReturnValue(true);

    const { initBridge } = await import("./bridgeStore.svelte");
    const bridge = initBridge();

    expect(bridge.authentication).toEqual({ status: "checking" });
    current.resolve(
      new Response(JSON.stringify({ status: "anonymous" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    bridge.disconnect();
  });

  it("does not accept the Web-only checking state from auth current", async () => {
    const clientResponse = deferred<Response>();
    const fetchImpl = vi.fn<typeof fetch>(async input => {
      if (String(input) === "/api/auth/current") {
        return new Response(
          JSON.stringify({
            status: "checking",
            loginError: { code: "provider_login_failed" },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (String(input) === "/api/clients") return clientResponse.promise;
      return new Response(null, { status: 202 });
    });
    vi.stubGlobal("fetch", fetchImpl);
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.spyOn(navigator, "sendBeacon").mockReturnValue(true);

    const { initBridge, parseBridgeAuthenticationState } = await import(
      "./bridgeStore.svelte"
    );
    expect(
      parseBridgeAuthenticationState({ status: "checking" }),
    ).toBeUndefined();
    const bridge = initBridge();
    await vi.waitFor(() =>
      expect(fetchImpl).toHaveBeenCalledWith(
        "/api/clients",
        expect.objectContaining({ method: "POST" }),
      ),
    );

    expect(bridge.authentication).toEqual({ status: "checking" });
    expect(bridge.notifications).toEqual([]);
    clientResponse.resolve(new Response(null, { status: 503 }));
    bridge.disconnect();
  });

  it("does not accept the Web-only checking state from client creation", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async input => {
      if (String(input) === "/api/auth/current") {
        return new Response(JSON.stringify({ status: "anonymous" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (String(input) === "/api/clients") {
        return new Response(
          JSON.stringify({
            client: { id: "client-1" },
            eventsUrl: "/events",
            messagesUrl: "/messages",
            authentication: { status: "checking" },
          }),
          { status: 201, headers: { "content-type": "application/json" } },
        );
      }
      return new Response(null, { status: 202 });
    });
    vi.stubGlobal("fetch", fetchImpl);
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.spyOn(navigator, "sendBeacon").mockReturnValue(true);

    const { initBridge } = await import("./bridgeStore.svelte");
    const bridge = initBridge();
    await vi.waitFor(() => expect(eventSources).toHaveLength(1));
    eventSources[0]!.open();
    await vi.waitFor(() => expect(bridge.connectionStatus).toBe("connected"));

    expect(bridge.authentication).toEqual({ status: "anonymous" });
    bridge.disconnect();
  });

  it("exposes the server-projected User summary to the application", async () => {
    const bridge = await connectBridge(
      Promise.resolve(new Response(null, { status: 202 })),
      undefined,
      {
        id: "user-alice",
        username: "Alice",
        avatarUrl: "https://example.test/alice.png",
      },
    );

    expect(bridge.currentUser).toEqual({
      id: "user-alice",
      username: "Alice",
      avatarUrl: "https://example.test/alice.png",
    });
    bridge.disconnect();
  });

  it("exposes Anonymous as a normal server-projected authentication state", async () => {
    const bridge = await connectBridge(
      Promise.resolve(new Response(null, { status: 202 })),
      undefined,
      undefined,
      { status: "anonymous" },
    );

    expect(bridge.authentication).toEqual({ status: "anonymous" });
    expect(bridge.currentUser).toBeUndefined();
    bridge.disconnect();
  });

  it.each([
    ["authorization_invalid", "本次授权无效或已失效，请重新登录"],
    ["provider_unavailable", "登录服务暂时无法响应，请稍后重试"],
    ["provider_identity_invalid", "无法验证登录身份，请重新登录；持续失败请联系管理员"],
    ["login_configuration_error", "登录服务配置异常，请联系管理员"],
    ["login_session_failed", "无法建立登录会话，请重试；持续失败请联系管理员"],
    ["user_data_transfer_failed", "无法接续登录前的数据，请重试；持续失败请联系管理员"],
    ["login_failed", "登录未完成，请重试；持续失败请联系管理员"],
  ] as const)("shows a recoverable %s from one-time auth current state", async (code, message) => {
    let currentReads = 0;
    const clientResponse = deferred<Response>();
    const fetchImpl = vi.fn<typeof fetch>(async input => {
      if (String(input) === "/api/auth/current") {
        currentReads += 1;
        return new Response(
          JSON.stringify({
            status: "anonymous",
            loginError: { code },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (String(input) === "/api/clients") {
        return clientResponse.promise;
      }
      return new Response(null, { status: 202 });
    });
    vi.stubGlobal("fetch", fetchImpl);
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.spyOn(navigator, "sendBeacon").mockReturnValue(true);
    window.history.replaceState({}, "", "/chat");
    const assign = vi
      .spyOn(window.location, "assign")
      .mockImplementation(() => undefined);

    const { initBridge } = await import("./bridgeStore.svelte");
    const bridge = initBridge();
    await vi.waitFor(() =>
      expect(bridge.authentication).toEqual({
        status: "anonymous",
        loginError: { code },
      }),
    );
    expect(bridge.notifications.at(-1)).toMatchObject({
      message,
      notifyType: "error",
    });
    clientResponse.resolve(
      new Response(
        JSON.stringify({
          client: { id: "client-1" },
          eventsUrl: "/events",
          messagesUrl: "/messages",
          authentication: { status: "anonymous" },
        }),
        { status: 201, headers: { "content-type": "application/json" } },
      ),
    );
    await vi.waitFor(() => expect(eventSources).toHaveLength(1));
    eventSources[0]!.open();
    await vi.waitFor(() => expect(bridge.connectionStatus).toBe("connected"));

    expect(currentReads).toBe(1);
    expect(bridge.authentication).toEqual({ status: "anonymous" });
    expect(bridge.notifications.at(-1)).toMatchObject({
      message,
      notifyType: "error",
    });
    bridge.login();
    expect(assign).toHaveBeenCalledWith(
      "/api/auth/login?returnTo=%2Fchat",
    );
    bridge.disconnect();
  });

  it("maps unknown error strings to a safe fallback and ignores malformed errors", async () => {
    const bridge = await connectBridge(Promise.resolve(new Response(null, { status: 202 })));
    const { parseBridgeAuthenticationState } = await import("./bridgeStore.svelte");
    expect(parseBridgeAuthenticationState({
      status: "anonymous", loginError: { code: "raw-secret-value" },
    })).toEqual({ status: "anonymous", loginError: { code: "login_failed" } });
    for (const code of [null, undefined, 42, {}, ""]) {
      expect(parseBridgeAuthenticationState({ status: "anonymous", loginError: { code } }))
        .toEqual({ status: "anonymous" });
    }
    bridge.disconnect();
  });

  it("restores reauthentication from auth current without creating a Bridge Client", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async input => {
      if (String(input) === "/api/auth/current") {
        return new Response(JSON.stringify({ status: "reauth_required" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      return new Response(null, { status: 500 });
    });
    vi.stubGlobal("fetch", fetchImpl);
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.spyOn(navigator, "sendBeacon").mockReturnValue(true);

    const { initBridge } = await import("./bridgeStore.svelte");
    const bridge = initBridge();

    await vi.waitFor(() =>
      expect(bridge.authentication).toEqual({ status: "reauth_required" }),
    );
    expect(fetchImpl).not.toHaveBeenCalledWith(
      "/api/clients",
      expect.anything(),
    );
    expect(eventSources).toHaveLength(0);
    bridge.disconnect();
  });

  it("applies a server-projected reauthentication state from SSE without reconnecting", async () => {
    const bridge = await connectBridge(
      Promise.resolve(new Response(null, { status: 202 })),
      undefined,
      { id: "user-alice", username: "Alice" },
      {
        status: "authenticated",
        user: { id: "user-alice", username: "Alice" },
      },
    );

    eventSources[0]!.send({
      type: "authentication",
      payload: { status: "reauth_required" },
    });
    await vi.waitFor(() =>
      expect(bridge.authentication).toEqual({ status: "reauth_required" }),
    );
    eventSources[0]!.close();
    eventSources[0]!.dispatchEvent(new Event("error"));
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(bridge.currentUser).toBeUndefined();
    expect(eventSources).toHaveLength(1);
    bridge.disconnect();
  });

  it("ignores the Web-only checking state when it arrives over SSE", async () => {
    const bridge = await connectBridge(
      Promise.resolve(new Response(null, { status: 202 })),
      undefined,
      undefined,
      { status: "anonymous" },
    );

    eventSources[0]!.send({
      type: "authentication",
      payload: { status: "checking" },
    });
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(bridge.authentication).toEqual({ status: "anonymous" });
    bridge.disconnect();
  });

  it("starts login with the current same-origin Dano return path", async () => {
    const bridge = await connectBridge(
      Promise.resolve(new Response(null, { status: 202 })),
      undefined,
      undefined,
      { status: "anonymous" },
    );
    window.history.replaceState({}, "", "/chat?session=one#latest");
    const assign = vi
      .spyOn(window.location, "assign")
      .mockImplementation(() => undefined);

    bridge.login();

    expect(assign).toHaveBeenCalledWith(
      "/api/auth/login?returnTo=%2Fchat%3Fsession%3Done%23latest",
    );
    bridge.disconnect();
  });

  it("restores the latest existing default workspace session after a page return", async () => {
    const latestSessionPath = "/users/current/sessions/transferred.jsonl";
    const { bridge, commands } = await connectWithDefaultWorkspaceSessions([
      {
        id: "transferred-session",
        name: "Transferred conversation",
        path: latestSessionPath,
        updatedAt: "2026-08-13T01:00:00.000Z",
        content: "Conversation from before login",
      },
      {
        id: "older-session",
        name: "Older conversation",
        path: "/users/current/sessions/older.jsonl",
        updatedAt: "2026-08-12T01:00:00.000Z",
        content: "Older conversation",
      },
    ]);

    await vi.waitFor(() =>
      expect(bridge.activeSessionPath).toBe(latestSessionPath),
    );
    expect(bridge.transcript).toEqual([
      expect.objectContaining({ content: "Conversation from before login" }),
    ]);
    expect(commands).toContainEqual(
      expect.objectContaining({ type: "list_sessions", limit: 1 }),
    );
    expect(commands).toContainEqual(
      expect.objectContaining({
        type: "switch_session",
        sessionPath: latestSessionPath,
      }),
    );
    expect(commands).not.toContainEqual(
      expect.objectContaining({ type: "new_session" }),
    );
    bridge.disconnect();
  });

  it("opens a new chat after successful login even when history exists", async () => {
    window.history.replaceState({}, "", "/chat?dano_new_chat=1&keep=yes#anchor");
    const { bridge, commands, newSessionPath } = await connectWithDefaultWorkspaceSessions([{
      id: "old", name: "History", path: "/users/current/sessions/old.jsonl",
      updatedAt: "2026-09-23T01:00:00.000Z", content: "Preserved history",
    }], "/users/current/sessions/old.jsonl");
    await vi.waitFor(() => expect(bridge.activeSessionPath).toBe(newSessionPath));
    expect(commands.filter(command => command.type === "new_session")).toHaveLength(1);
    expect(commands.some(command => command.type === "switch_session")).toBe(false);
    expect(bridge.transcript).toEqual([]);
    expect(window.location.pathname + window.location.search + window.location.hash).toBe("/chat?keep=yes#anchor");
    eventSources[0]!.open();
    await vi.waitFor(() => expect(commands).toContainEqual(
      expect.objectContaining({ type: "switch_session", sessionPath: newSessionPath }),
    ));
    expect(commands.filter(command => command.type === "new_session")).toHaveLength(1);
    bridge.disconnect();
  });

  it("retains the login new-chat marker if creating the chat is cancelled", async () => {
    window.history.replaceState({}, "", "/chat?dano_new_chat=1");
    const { bridge } = await connectWithDefaultWorkspaceSessions([], undefined, true);
    await vi.waitFor(() => expect(bridge.notifications.some(n => n.notifyType === "error")).toBe(true));
    expect(window.location.search).toBe("?dano_new_chat=1");
    bridge.disconnect();
    window.history.replaceState({}, "", "/chat");
  });

  it("creates the default workspace session only when no session exists", async () => {
    const { bridge, commands, newSessionPath } =
      await connectWithDefaultWorkspaceSessions([]);

    await vi.waitFor(() =>
      expect(bridge.activeSessionPath).toBe(newSessionPath),
    );
    expect(commands).toContainEqual(
      expect.objectContaining({ type: "list_sessions", limit: 1 }),
    );
    expect(commands).toContainEqual(
      expect.objectContaining({ type: "new_session" }),
    );
    bridge.disconnect();
  });

  it("posts same-origin logout and reloads into a fresh Anonymous User", async () => {
    const bridge = await connectBridge(
      Promise.resolve(new Response(null, { status: 202 })),
      undefined,
      { id: "user-alice", username: "Alice" },
      {
        status: "authenticated",
        user: { id: "user-alice", username: "Alice" },
      },
    );
    const logoutFetch = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify({ status: "anonymous" })));
    vi.stubGlobal("fetch", logoutFetch);
    const reload = vi
      .spyOn(window.location, "reload")
      .mockImplementation(() => undefined);

    await bridge.logout();

    expect(logoutFetch).toHaveBeenCalledWith("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    });
    expect(reload).toHaveBeenCalledOnce();
    bridge.disconnect();
  });

  it("serializes first-client bootstrap across tabs before creating a guest", async () => {
    const request = vi.fn(
      async (_name: string, callback: () => Promise<Response>) => callback(),
    );
    vi.stubGlobal("navigator", {
      locks: { request },
      sendBeacon: () => true,
    });

    const bridge = await connectBridge(
      Promise.resolve(new Response(null, { status: 202 })),
      undefined,
      undefined,
      { status: "anonymous" },
    );

    expect(request).toHaveBeenCalledWith(
      "dano-anonymous-user-bootstrap",
      expect.any(Function),
    );
    bridge.disconnect();
  });

  it("waits for HTTP 202 without restoring pending after an earlier server event", async () => {
    const promptResponse = deferred<Response>();
    const bridge = await connectBridge(promptResponse.promise);

    const submitted = bridge.sendPrompt("ordinary short prompt");
    await vi.waitFor(() => expect(bridge.isPromptPending).toBe(true));

    eventSources[0]!.send({
      type: "event",
      payload: { type: "agent_end", sessionPath: null },
    });
    expect(bridge.isPromptPending).toBe(false);

    promptResponse.resolve(new Response(null, { status: 202 }));
    await expect(submitted).resolves.toBe(true);
    expect(bridge.isPromptPending).toBe(false);

    bridge.disconnect();
  });

  it("keeps the prompt unaccepted and exposes an actionable HTTP error", async () => {
    const bridge = await connectBridge(
      Promise.resolve(
        new Response(JSON.stringify({ error: "RECONNECT_REQUIRED" }), {
          status: 409,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(bridge.sendPrompt("retry me")).resolves.toBe(false);
    expect(bridge.isPromptPending).toBe(false);
    expect(bridge.connectionStatus).toBe("disconnected");
    expect(bridge.notifications.at(-1)).toMatchObject({
      notifyType: "error",
      message: expect.stringMatching(/刷新|refresh/i),
    });

    bridge.disconnect();
  });

  it("waits for streaming follow-up acknowledgement and rolls back only its optimistic message", async () => {
    const promptResponse = deferred<Response>();
    const bridge = await connectBridge(promptResponse.promise);
    eventSources[0]!.send({
      type: "event",
      payload: { type: "agent_start", sessionPath: null },
    });
    expect(bridge.isStreaming).toBe(true);

    const submitted = bridge.sendPrompt(
      "queued by this submission",
      undefined,
      undefined,
      "followUp",
    );
    await vi.waitFor(() =>
      expect(bridge.queuedUserMessages).toMatchObject([
        { text: "queued by this submission", queueType: "followUp" },
      ]),
    );

    eventSources[0]!.send({
      type: "event",
      payload: {
        type: "queue_update",
        sessionPath: null,
        steering: [],
        followUp: [
          {
            text: "authoritative queued message",
            images: [],
            timestamp: 123,
            queueType: "followUp",
          },
        ],
      },
    });
    expect(bridge.queuedUserMessages).toMatchObject([
      { text: "authoritative queued message", queueType: "followUp" },
    ]);

    promptResponse.resolve(
      new Response(JSON.stringify({ error: "RECONNECT_REQUIRED" }), {
        status: 409,
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(submitted).resolves.toBe(false);
    expect(bridge.queuedUserMessages).toMatchObject([
      { text: "authoritative queued message", queueType: "followUp" },
    ]);

    bridge.disconnect();
  });

  it("rolls back rejected steering and sends one request on explicit retry", async () => {
    const promptResponse = vi
      .fn<() => Promise<Response>>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: "bridge unavailable" }), {
          status: 503,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 202 }));
    const bridge = await connectBridge(promptResponse);
    eventSources[0]!.send({
      type: "event",
      payload: { type: "agent_start", sessionPath: null },
    });

    await expect(
      bridge.sendPrompt("steer this turn", undefined, undefined, "steer"),
    ).resolves.toBe(false);
    expect(bridge.queuedUserMessages).toEqual([]);
    expect(promptResponse).toHaveBeenCalledTimes(1);

    await expect(
      bridge.sendPrompt("steer this turn", undefined, undefined, "steer"),
    ).resolves.toBe(true);
    expect(bridge.queuedUserMessages).toMatchObject([
      { text: "steer this turn", queueType: "steering" },
    ]);
    expect(promptResponse).toHaveBeenCalledTimes(2);

    bridge.disconnect();
  });

  it.each([
    ["follow-up", "followUp" as const, "followUp" as const],
    ["steering", "steer" as const, "steering" as const],
  ])(
    "aborts a timed-out streaming %s, returns false for Composer, and preserves the authoritative queue",
    async (_label, streamingBehavior, queueType) => {
      let requestSignal: AbortSignal | undefined;
      const bridge = await connectBridge(
        new Promise<Response>(() => {}),
        init => {
          requestSignal = init?.signal ?? undefined;
        },
      );
      eventSources[0]!.send({
        type: "event",
        payload: { type: "agent_start", sessionPath: null },
      });
      vi.useFakeTimers();

      const submitted = bridge.sendPrompt(
        "times out in the browser queue",
        undefined,
        undefined,
        streamingBehavior,
      );
      await vi.advanceTimersByTimeAsync(0);
      expect(bridge.queuedUserMessages).toMatchObject([
        { text: "times out in the browser queue", queueType },
      ]);

      eventSources[0]!.send({
        type: "event",
        payload: {
          type: "queue_update",
          sessionPath: null,
          steering:
            queueType === "steering"
              ? [
                  {
                    text: "authoritative queued message",
                    images: [],
                    timestamp: 123,
                    queueType: "steering",
                  },
                ]
              : [],
          followUp:
            queueType === "followUp"
              ? [
                  {
                    text: "authoritative queued message",
                    images: [],
                    timestamp: 123,
                    queueType: "followUp",
                  },
                ]
              : [],
        },
      });

      await vi.advanceTimersByTimeAsync(10_000);

      await expect(submitted).resolves.toBe(false);
      expect(requestSignal?.aborted).toBe(true);
      expect(bridge.queuedUserMessages).toMatchObject([
        { text: "authoritative queued message", queueType },
      ]);
      expect(bridge.notifications.at(-1)).toMatchObject({
        notifyType: "error",
        message: expect.stringMatching(/10|超时|timed out/i),
      });

      bridge.disconnect();
    },
  );
});
