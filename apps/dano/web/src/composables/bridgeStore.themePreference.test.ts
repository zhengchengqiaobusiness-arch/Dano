/** @vitest-environment happy-dom */

import { afterEach, describe, expect, it, vi } from "vitest";

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

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }

  fail() {
    this.readyState = FakeEventSource.CLOSED;
    this.dispatchEvent(new Event("error"));
  }
}

const eventSources: FakeEventSource[] = [];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(r => {
    resolve = r;
  });
  return { promise, resolve };
}

async function connectBridge(fetchImpl: typeof fetch) {
  vi.stubGlobal("fetch", vi.fn(fetchImpl));
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.spyOn(navigator, "sendBeacon").mockReturnValue(true);
  const { initBridge } = await import("./bridgeStore.svelte");
  const bridge = initBridge();
  await vi.waitFor(() => expect(eventSources).toHaveLength(1));
  eventSources[0]!.open();
  await vi.waitFor(() => expect(bridge.connectionStatus).toBe("connected"));
  return bridge;
}

function clientResponse() {
  return new Response(
    JSON.stringify({
      client: { id: "client-1" },
      eventsUrl: "/events",
      messagesUrl: "/messages",
    }),
    { status: 201, headers: { "content-type": "application/json" } },
  );
}

describe("Bridge Theme Color preference", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.resetModules();
    eventSources.length = 0;
  });

  it("loads the authenticated User Theme Color after connecting", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async input => {
      if (String(input) === "/api/clients") return clientResponse();
      if (String(input) === "/api/clients/client-1/preferences/theme") {
        return new Response(JSON.stringify({ accentColorPreset: "pink" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      return new Response(null, { status: 202 });
    });
    const bridge = await connectBridge(fetchImpl);

    await vi.waitFor(() => expect(bridge.accentColorPreset).toBe("pink"));
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/clients/client-1/preferences/theme",
      expect.objectContaining({ method: "GET" }),
    );
    bridge.disconnect();
  });

  it("applies immediately, serializes saves, and keeps the optimistic value on failure", async () => {
    let firstSaveFinished = false;
    const saveOrder: string[] = [];
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input) === "/api/clients") return clientResponse();
      if (String(input) === "/api/clients/client-1/preferences/theme") {
        if (init?.method === "GET") {
          return new Response(JSON.stringify({ accentColorPreset: "default" }), {
            status: 200,
          });
        }
        const body = JSON.parse(String(init?.body)) as { accentColorPreset: string };
        saveOrder.push(body.accentColorPreset);
        if (body.accentColorPreset === "blue") {
          await new Promise(resolve => setTimeout(resolve, 10));
          firstSaveFinished = true;
          return new Response(JSON.stringify(body), { status: 200 });
        }
        expect(firstSaveFinished).toBe(true);
        return new Response(JSON.stringify({ error: "disk full" }), { status: 500 });
      }
      return new Response(null, { status: 202 });
    });
    const bridge = await connectBridge(fetchImpl);
    await vi.waitFor(() =>
      expect(fetchImpl).toHaveBeenCalledWith(
        "/api/clients/client-1/preferences/theme",
        expect.objectContaining({ method: "GET" }),
      ),
    );
    await new Promise(resolve => setTimeout(resolve, 0));

    const blueSave = bridge.setAccentColorPreset("blue");
    expect(bridge.accentColorPreset).toBe("blue");
    const purpleSave = bridge.setAccentColorPreset("purple");
    expect(bridge.accentColorPreset).toBe("purple");

    await expect(blueSave).resolves.toBe(true);
    await expect(purpleSave).resolves.toBe(false);
    expect(saveOrder).toEqual(["blue", "purple"]);
    expect(bridge.accentColorPreset).toBe("purple");
    expect(bridge.notifications.at(-1)).toMatchObject({
      notifyType: "error",
      message: expect.stringMatching(/主题色|Theme color/i),
    });
    bridge.disconnect();
  });

  it("does not let a late initial read replace a newer optimistic selection", async () => {
    const initialRead = deferred<Response>();
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input) === "/api/clients") return clientResponse();
      if (String(input) === "/api/clients/client-1/preferences/theme") {
        if (init?.method === "GET") return initialRead.promise;
        return new Response(String(init?.body), { status: 200 });
      }
      return new Response(null, { status: 202 });
    });
    const bridge = await connectBridge(fetchImpl);

    const save = bridge.setAccentColorPreset("blue");
    expect(bridge.accentColorPreset).toBe("blue");
    initialRead.resolve(
      new Response(JSON.stringify({ accentColorPreset: "pink" }), {
        status: 200,
      }),
    );

    await expect(save).resolves.toBe(true);
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(bridge.accentColorPreset).toBe("blue");
    bridge.disconnect();
  });

  it("clears the previous User value and ignores its late read after disconnect", async () => {
    const initialRead = deferred<Response>();
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input) === "/api/clients") return clientResponse();
      if (
        String(input) === "/api/clients/client-1/preferences/theme" &&
        init?.method === "GET"
      ) {
        return initialRead.promise;
      }
      return new Response(null, { status: 202 });
    });
    const bridge = await connectBridge(fetchImpl);
    await vi.waitFor(() =>
      expect(fetchImpl).toHaveBeenCalledWith(
        "/api/clients/client-1/preferences/theme",
        expect.objectContaining({ method: "GET" }),
      ),
    );

    bridge.setAccentColorPreset("purple");
    expect(bridge.accentColorPreset).toBe("purple");
    bridge.disconnect();
    expect(bridge.accentColorPreset).toBe("default");

    initialRead.resolve(
      new Response(JSON.stringify({ accentColorPreset: "pink" }), {
        status: 200,
      }),
    );
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(bridge.accentColorPreset).toBe("default");
  });

  it("isolates a reconnected User from the previous User and its late read", async () => {
    const firstUserRead = deferred<Response>();
    const firstUserSave = deferred<Response>();
    let createdClients = 0;
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input) === "/api/clients") {
        createdClients += 1;
        return new Response(
          JSON.stringify({
            client: { id: `client-${createdClients}` },
            eventsUrl: `/events-${createdClients}`,
            messagesUrl: `/messages-${createdClients}`,
          }),
          { status: 201, headers: { "content-type": "application/json" } },
        );
      }
      if (
        String(input) === "/api/clients/client-1/preferences/theme" &&
        init?.method === "GET"
      ) {
        return firstUserRead.promise;
      }
      if (
        String(input) === "/api/clients/client-1/preferences/theme" &&
        init?.method === "PUT"
      ) {
        return firstUserSave.promise;
      }
      if (
        String(input) === "/api/clients/client-2/preferences/theme" &&
        init?.method === "GET"
      ) {
        return new Response(JSON.stringify({ accentColorPreset: "yellow" }), {
          status: 200,
        });
      }
      if (
        String(input) === "/api/clients/client-2/preferences/theme" &&
        init?.method === "PUT"
      ) {
        return new Response(String(init.body), { status: 200 });
      }
      return new Response(null, { status: 202 });
    });
    const bridge = await connectBridge(fetchImpl);
    const oldUserSave = bridge.setAccentColorPreset("purple");
    await vi.waitFor(() =>
      expect(fetchImpl).toHaveBeenCalledWith(
        "/api/clients/client-1/preferences/theme",
        expect.objectContaining({ method: "PUT" }),
      ),
    );

    eventSources[0]!.fail();
    expect(bridge.accentColorPreset).toBe("default");
    const reconnecting = bridge.reconnect();
    await vi.waitFor(() => expect(eventSources).toHaveLength(2));
    eventSources[1]!.open();
    await reconnecting;
    await vi.waitFor(() => expect(bridge.accentColorPreset).toBe("yellow"));

    const newUserSave = bridge.setAccentColorPreset("blue");
    await vi.waitFor(() =>
      expect(fetchImpl).toHaveBeenCalledWith(
        "/api/clients/client-2/preferences/theme",
        expect.objectContaining({ method: "PUT" }),
      ),
    );
    await expect(newUserSave).resolves.toBe(true);

    firstUserSave.resolve(
      new Response(JSON.stringify({ error: "old user disk full" }), {
        status: 500,
      }),
    );
    await expect(oldUserSave).resolves.toBe(false);
    expect(bridge.notifications).toEqual([]);
    expect(bridge.accentColorPreset).toBe("blue");

    firstUserRead.resolve(
      new Response(JSON.stringify({ accentColorPreset: "pink" }), {
        status: 200,
      }),
    );
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(bridge.accentColorPreset).toBe("blue");
    bridge.disconnect();
  });
});
