import { describe, expect, it, vi } from "vitest";
import { BridgeEventBus } from "../bridge-event-bus.js";
import { DEFAULT_BRIDGE_CONFIG, type WsClient } from "../types.js";

describe("BridgeEventBus", () => {
  const createClient = (id: string, seq: number): WsClient => ({
    id,
    seq,
    connectedAt: new Date().toISOString(),
  });

  describe("subscribe / emit", () => {
    it("should emit events to subscribers", () => {
      const bus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
      const handler = vi.fn();

      const unsubscribe = bus.subscribe(handler);
      bus.emit({ type: "server_start", host: "localhost", port: 8080 });

      expect(handler).toHaveBeenCalledTimes(1);
      expect(handler).toHaveBeenCalledWith({
        type: "server_start",
        host: "localhost",
        port: 8080,
      });

      unsubscribe();
      bus.dispose();
    });

    it("should support multiple subscribers", () => {
      const bus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
      const handler1 = vi.fn();
      const handler2 = vi.fn();

      bus.subscribe(handler1);
      bus.subscribe(handler2);
      bus.emit({ type: "server_start", host: "localhost", port: 8080 });

      expect(handler1).toHaveBeenCalledTimes(1);
      expect(handler2).toHaveBeenCalledTimes(1);

      bus.dispose();
    });

    it("should allow unsubscribing", () => {
      const bus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
      const handler = vi.fn();

      const unsubscribe = bus.subscribe(handler);
      bus.emit({ type: "server_start", host: "localhost", port: 8080 });
      expect(handler).toHaveBeenCalledTimes(1);

      unsubscribe();
      bus.emit({ type: "server_start", host: "localhost", port: 9000 });
      expect(handler).toHaveBeenCalledTimes(1);

      bus.dispose();
    });

    it("should not break when subscriber throws", () => {
      const bus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
      const errorHandler = vi.fn(() => {
        throw new Error("Subscriber error");
      });
      const goodHandler = vi.fn();

      bus.subscribe(errorHandler);
      bus.subscribe(goodHandler);
      bus.emit({ type: "server_start", host: "localhost", port: 8080 });

      expect(errorHandler).toHaveBeenCalled();
      expect(goodHandler).toHaveBeenCalled();

      bus.dispose();
    });
  });

  describe("registerClient / unregisterClient", () => {
    it("should unregister a client", () => {
      const bus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
      const client = createClient("c1", 1);
      const send = vi.fn();

      bus.registerClient(client, send);
      bus.unregisterClient("c1");

      // After unregister, broadcast should not reach the client
      bus.broadcast({ type: "test" });
      expect(send).not.toHaveBeenCalled();

      bus.dispose();
    });
  });

  describe("broadcast", () => {
    it("should broadcast events to all clients", () => {
      const bus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
      const client1 = createClient("c1", 1);
      const client2 = createClient("c2", 2);
      const send1 = vi.fn();
      const send2 = vi.fn();

      bus.registerClient(client1, send1);
      bus.registerClient(client2, send2);
      bus.broadcast({ type: "agent_start" });

      const expectedMessage = JSON.stringify({
        type: "event",
        payload: { type: "agent_start" },
      });
      expect(send1).toHaveBeenCalledWith(expectedMessage);
      expect(send2).toHaveBeenCalledWith(expectedMessage);

      bus.dispose();
    });

    it("should buffer messages when send fails", () => {
      const bus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
      const client = createClient("c1", 1);
      const send = vi.fn().mockImplementation(() => {
        throw new Error("Send failed");
      });

      bus.registerClient(client, send);
      bus.broadcast({ type: "agent_start" });

      // Send is attempted (initial attempt + flush attempt)
      expect(send).toHaveBeenCalledTimes(2);
      expect(bus.getClientQueueDepth("c1")).toBe(1);

      bus.dispose();
    });
  });

  describe("backpressure handling", () => {
    it("should drop oldest messages when buffer is full", () => {
      const config = { ...DEFAULT_BRIDGE_CONFIG, clientBufferSize: 3 };
      const bus = new BridgeEventBus(config);
      const client = createClient("c1", 1);
      const send = vi.fn().mockImplementation(() => {
        throw new Error("Send failed");
      });

      bus.registerClient(client, send);

      // Fill buffer beyond capacity
      bus.broadcast({ type: "msg1" });
      bus.broadcast({ type: "msg2" });
      bus.broadcast({ type: "msg3" });
      bus.broadcast({ type: "msg4" }); // Should drop msg1

      expect(bus.getClientQueueDepth("c1")).toBe(3);

      bus.dispose();
    });

    it("should report exact queue depth per client", () => {
      const config = { ...DEFAULT_BRIDGE_CONFIG, clientBufferSize: 10 };
      const bus = new BridgeEventBus(config);
      const client1 = createClient("c1", 1);
      const client2 = createClient("c2", 2);
      const failingSend = vi.fn().mockImplementation(() => {
        throw new Error("Send failed");
      });

      bus.registerClient(client1, failingSend);
      bus.registerClient(client2, vi.fn());
      bus.broadcast({ type: "agent_start" });
      bus.broadcast({ type: "agent_end" });

      expect(bus.getQueueStats()).toEqual([
        { clientId: "c1", depth: 2, maxDepth: 10 },
        { clientId: "c2", depth: 0, maxDepth: 10 },
      ]);

      bus.dispose();
    });
  });

  describe("dispose", () => {
    it("should clean up all clients and handlers", () => {
      const bus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
      const client = createClient("c1", 1);
      const handler = vi.fn();

      bus.subscribe(handler);
      bus.registerClient(client, vi.fn());
      bus.dispose();

      // After dispose, nothing should happen
      bus.emit({ type: "server_start", host: "localhost", port: 8080 });
      bus.broadcast({ type: "test" });

      expect(handler).not.toHaveBeenCalled();
    });
  });

  describe("event envelope", () => {
    it("should wrap events in ServerMessage envelope", () => {
      const bus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
      const client = createClient("c1", 1);
      const send = vi.fn();

      bus.registerClient(client, send);
      bus.broadcast({
        type: "turn_start",
        turnIndex: 5,
        timestamp: Date.now(),
      });

      const sentData = send.mock.calls[0][0] as string;
      const parsed = JSON.parse(sentData);

      expect(parsed).toHaveProperty("type", "event");
      expect(parsed).toHaveProperty("payload");
      expect(parsed.payload).toHaveProperty("type", "turn_start");
      expect(parsed.payload).toHaveProperty("turnIndex", 5);

      bus.dispose();
    });
  });

  describe("observability", () => {
    it("should track per-client queue depth", () => {
      const bus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
      const client = createClient("c1", 1);
      const send = vi.fn().mockImplementation(() => {
        throw new Error("Send failed");
      });

      bus.registerClient(client, send);

      expect(bus.getClientQueueDepth("c1")).toBe(0);

      bus.broadcast({ type: "msg1" });
      expect(bus.getClientQueueDepth("c1")).toBe(1);

      bus.broadcast({ type: "msg2" });
      expect(bus.getClientQueueDepth("c1")).toBe(2);

      bus.dispose();
    });
  });
});
