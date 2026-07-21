/** @vitest-environment happy-dom */

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

async function connectBridge(
  promptResponse: Promise<Response> | (() => Promise<Response>),
) {
  const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input) === "/api/clients") {
      return new Response(
        JSON.stringify({
          client: { id: "client-1" },
          eventsUrl: "/events",
          messagesUrl: "/messages",
        }),
        { status: 201, headers: { "content-type": "application/json" } },
      );
    }
    const envelope = JSON.parse(String(init?.body ?? "{}")) as {
      payload?: { type?: string };
    };
    if (envelope.payload?.type === "prompt") {
      return typeof promptResponse === "function"
        ? promptResponse()
        : promptResponse;
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
  return bridge;
}

describe("Bridge prompt acceptance", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
    eventSources.length = 0;
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
});
