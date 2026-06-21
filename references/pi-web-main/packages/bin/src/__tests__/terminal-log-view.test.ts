import {
  DEFAULT_BRIDGE_CONFIG,
  type BridgeEvent,
  type WsClient,
} from "@pi-web/bridge/types";
import { describe, expect, it, vi } from "vitest";
import { createBridgeTerminalView } from "../terminal-log-view.js";

describe("createBridgeTerminalView", () => {
  const client: WsClient = {
    id: "client-1234567890",
    seq: 1,
    connectedAt: new Date().toISOString(),
  };

  it("requests exit when Ctrl+C input is received", () => {
    const view = createBridgeTerminalView(
      () => () => {},
      () => ({ status: "running", host: "127.0.0.1", port: 3000 }),
      () => [],
      DEFAULT_BRIDGE_CONFIG,
    );

    expect(view.shouldExit()).toBe(false);
    view.handleInput("\u0003");
    expect(view.shouldExit()).toBe(true);
  });

  it("calls onUpdate when bridge events arrive", () => {
    let handler: ((event: BridgeEvent) => void) | undefined;
    const onUpdate = vi.fn();
    const unsubscribe = vi.fn();

    const view = createBridgeTerminalView(
      eventHandler => {
        handler = eventHandler;
        return unsubscribe;
      },
      () => ({ status: "running", host: "127.0.0.1", port: 3000 }),
      () => [],
      DEFAULT_BRIDGE_CONFIG,
      onUpdate,
    );

    handler?.({ type: "server_start", host: "127.0.0.1", port: 3000 });

    expect(onUpdate).toHaveBeenCalledWith(true);
    view.dispose();
    expect(unsubscribe).toHaveBeenCalled();
  });

  it("forces a full redraw when client count changes", () => {
    let handler: ((event: BridgeEvent) => void) | undefined;
    const onUpdate = vi.fn();

    createBridgeTerminalView(
      eventHandler => {
        handler = eventHandler;
        return () => {};
      },
      () => ({ status: "running", host: "127.0.0.1", port: 3000 }),
      () => [client],
      DEFAULT_BRIDGE_CONFIG,
      onUpdate,
    );

    handler?.({ type: "client_connect", client });
    handler?.({ type: "client_disconnect", client, reason: "closed" });

    expect(onUpdate).toHaveBeenNthCalledWith(1, true);
    expect(onUpdate).toHaveBeenNthCalledWith(2, true);
  });

  it("keeps incremental redraws for log-only events", () => {
    let handler: ((event: BridgeEvent) => void) | undefined;
    const onUpdate = vi.fn();

    createBridgeTerminalView(
      eventHandler => {
        handler = eventHandler;
        return () => {};
      },
      () => ({ status: "running", host: "127.0.0.1", port: 3000 }),
      () => [client],
      DEFAULT_BRIDGE_CONFIG,
      onUpdate,
    );

    handler?.({
      type: "command_received",
      client,
      commandType: "ping",
      correlationId: "corr-12345678",
    });

    expect(onUpdate).toHaveBeenCalledWith(false);
  });
});
