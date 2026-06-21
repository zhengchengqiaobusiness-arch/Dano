import type {
  ExtensionAPI,
  ExtensionCommandContext,
} from "@earendil-works/pi-coding-agent";
import { BridgeServer } from "@pi-web/bridge/server";
import { DetachedSessionRegistry } from "@pi-web/bridge/session-registry";
import { DEFAULT_BRIDGE_CONFIG, type BridgeEvent } from "@pi-web/bridge/types";
import type { WsRpcAdapterContext } from "@pi-web/bridge/ws-rpc-adapter";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WebSocket } from "ws";
import {
  startBridge,
  type BridgeController,
  type BridgeDoneCallback,
} from "../lifecycle.js";
import {
  createBridgeSessionActions,
  createBridgeSessionEvents,
  createBridgeSessionState,
} from "../pi-live-session.js";

const waitForAsyncWork = (ms = 100) =>
  new Promise(resolve => setTimeout(resolve, ms));

describe("Bridge Lifecycle", () => {
  const createMockContext = (): WsRpcAdapterContext => {
    const sessionManager = {
      getCwd: vi.fn().mockReturnValue("/test/project"),
      getSessionDir: vi.fn().mockReturnValue("/test"),
      getSessionId: vi.fn().mockReturnValue("test-session"),
      getSessionFile: vi.fn().mockReturnValue("/test/session.json"),
      getLeafId: vi.fn().mockReturnValue(null),
      getLeafEntry: vi.fn().mockReturnValue(undefined),
      getEntry: vi.fn().mockReturnValue(undefined),
      getLabel: vi.fn().mockReturnValue(undefined),
      getBranch: vi.fn().mockReturnValue([]),
      getHeader: vi.fn().mockReturnValue(null),
      getEntries: vi.fn().mockReturnValue([]),
      getTree: vi.fn().mockReturnValue([]),
      getSessionName: vi.fn().mockReturnValue(undefined),
    };

    const model = {
      id: "test-model",
      name: "Test Model",
      api: "openai-responses",
      provider: "test",
      baseUrl: "https://example.com",
      reasoning: true,
      input: ["text"] as const,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 1000,
      maxTokens: 1000,
    };

    const pi = {
      sendUserMessage: vi.fn(),
      setModel: vi.fn().mockResolvedValue(true),
      setThinkingLevel: vi.fn(),
      getThinkingLevel: vi.fn().mockReturnValue("medium"),
      setSessionName: vi.fn(),
      getSessionName: vi.fn().mockReturnValue(undefined),
      getCommands: vi.fn().mockReturnValue([]),
      on: vi.fn(),
    } as unknown as ExtensionAPI;

    const ctx = {
      sessionManager,
      model,
      modelRegistry: {
        getAvailable: vi.fn().mockReturnValue([]),
      } as unknown as ExtensionCommandContext["modelRegistry"],
      isIdle: vi.fn().mockReturnValue(true),
      signal: undefined,
      abort: vi.fn(),
      compact: vi.fn(),
      shutdown: vi.fn(),
      hasPendingMessages: vi.fn().mockReturnValue(false),
      getContextUsage: vi
        .fn()
        .mockReturnValue({ tokens: 100, contextWindow: 1000, percent: 10 }),
      getSystemPrompt: vi.fn().mockReturnValue("test prompt"),
      cwd: "/test/project",
      ui: {
        custom: vi.fn(),
      },
      hasUI: true,
      waitForIdle: vi.fn().mockResolvedValue(undefined),
      newSession: vi.fn().mockResolvedValue({ cancelled: false }),
      fork: vi.fn().mockResolvedValue({ cancelled: false }),
      navigateTree: vi.fn().mockResolvedValue({ cancelled: false }),
      switchSession: vi.fn().mockResolvedValue({ cancelled: false }),
      reload: vi.fn().mockResolvedValue(undefined),
    } as unknown as ExtensionCommandContext;

    return {
      events: createBridgeSessionEvents(pi),
      state: createBridgeSessionState(ctx, pi),
      actions: createBridgeSessionActions(pi, ctx),
    };
  };

  let mockContext: WsRpcAdapterContext;
  let doneCallback: ReturnType<typeof vi.fn>;
  let controllers: BridgeController[];

  const originalSigintListeners: Array<NodeJS.SignalsListener> = [];

  beforeEach(() => {
    mockContext = createMockContext();
    doneCallback = vi.fn();
    controllers = [];

    const listeners = process.listeners("SIGINT");
    originalSigintListeners.length = 0;
    originalSigintListeners.push(...(listeners as any));
    listeners.forEach(listener => process.off("SIGINT", listener as any));
  });

  afterEach(async () => {
    for (const controller of controllers) {
      if (controller.getState().status === "running") {
        await controller.stop();
      }
    }

    process.removeAllListeners("SIGINT");
    originalSigintListeners.forEach(listener =>
      process.on("SIGINT", listener as any),
    );
  });

  describe("controller contract", () => {
    it("starts the bridge and exposes the active URL", async () => {
      const controller = await startBridge(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        mockContext,
        doneCallback as BridgeDoneCallback,
      );
      controllers.push(controller);

      const state = controller.getState();
      expect(state.status).toBe("running");
      if (state.status !== "running") {
        throw new Error("bridge did not start");
      }

      expect(controller.getBridgeUrl()).toBe(
        `http://${state.host}:${state.port}`,
      );
    });

    it("tracks connected clients through the controller API", async () => {
      const controller = await startBridge(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        mockContext,
        doneCallback as BridgeDoneCallback,
      );
      controllers.push(controller);

      const state = controller.getState();
      if (state.status !== "running") {
        throw new Error("bridge did not start");
      }

      const ws = new WebSocket(`ws://${state.host}:${state.port}/ws`);
      await new Promise<void>((resolve, reject) => {
        ws.once("open", () => resolve());
        ws.once("error", reject);
      });
      await waitForAsyncWork();

      const clients = controller.getClients();
      expect(clients).toHaveLength(1);
      expect(clients[0].id).toBeTruthy();
      expect(clients[0].seq).toBeGreaterThan(0);

      ws.close();
      await waitForAsyncWork();
      expect(controller.getClients()).toHaveLength(0);
    });

    it("notifies subscribers about shutdown and supports unsubscribe", async () => {
      const controller = await startBridge(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        mockContext,
        doneCallback as BridgeDoneCallback,
      );
      controllers.push(controller);

      const events: BridgeEvent[] = [];
      const unsubscribe = controller.subscribe(event => {
        events.push(event);
      });

      await controller.stop();
      expect(events.map(event => event.type)).toContain("server_stop");
      expect(events.map(event => event.type)).toContain("shutdown_complete");

      const nextController = await startBridge(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        mockContext,
        doneCallback as BridgeDoneCallback,
      );
      controllers.push(nextController);

      const unsubscribedEvents: BridgeEvent[] = [];
      const stopListening = nextController.subscribe(event => {
        unsubscribedEvents.push(event);
      });
      stopListening();
      unsubscribe();

      await nextController.stop();
      expect(unsubscribedEvents).toHaveLength(0);
    });
  });

  describe("shutdown", () => {
    it("stops gracefully and calls done once", async () => {
      const controller = await startBridge(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        mockContext,
        doneCallback as BridgeDoneCallback,
      );
      controllers.push(controller);

      await controller.stop();
      await controller.stop();
      await controller.stop();

      expect(controller.getState().status).toBe("stopped");
      expect(controller.getBridgeUrl()).toBeUndefined();
      expect(doneCallback).toHaveBeenCalledTimes(1);
    });

    it("waits for an in-flight shutdown when stop is called again", async () => {
      const originalStop = BridgeServer.prototype.stop;
      const stopSpy = vi
        .spyOn(BridgeServer.prototype, "stop")
        .mockImplementation(async function (this: BridgeServer) {
          await waitForAsyncWork(50);
          return originalStop.call(this);
        });

      try {
        const controller = await startBridge(
          { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
          mockContext,
          doneCallback as BridgeDoneCallback,
        );
        controllers.push(controller);

        const firstStop = controller.stop();
        const secondStop = controller.stop();
        await secondStop;
        await firstStop;

        expect(stopSpy).toHaveBeenCalledTimes(1);
        expect(controller.getState().status).toBe("stopped");
        expect(doneCallback).toHaveBeenCalledTimes(1);
      } finally {
        stopSpy.mockRestore();
      }
    });

    it("can skip process-level SIGINT capture for embedded /web usage", async () => {
      const controller = await startBridge(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        mockContext,
        doneCallback as BridgeDoneCallback,
        { captureSigint: false },
      );
      controllers.push(controller);

      expect(process.listeners("SIGINT")).toHaveLength(0);

      await controller.stop();
      expect(controller.getState().status).toBe("stopped");
      expect(doneCallback).toHaveBeenCalledTimes(1);
    });

    it("keeps an injected detached-session registry alive across restarts", async () => {
      const sessionRegistry = new DetachedSessionRegistry(
        mockContext.state.cwd,
      );
      const disposeSpy = vi.spyOn(sessionRegistry, "dispose");
      const controller = await startBridge(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        mockContext,
        doneCallback as BridgeDoneCallback,
        { captureSigint: false, sessionRegistry },
      );
      controllers.push(controller);

      await controller.stop();

      expect(disposeSpy).not.toHaveBeenCalled();

      sessionRegistry.dispose();
      disposeSpy.mockRestore();
    });

    it("shuts down on SIGINT", async () => {
      const controller = await startBridge(
        { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
        mockContext,
        doneCallback as BridgeDoneCallback,
      );
      controllers.push(controller);

      const events: BridgeEvent[] = [];
      controller.subscribe(event => {
        events.push(event);
      });

      process.emit("SIGINT");
      await waitForAsyncWork(200);

      expect(events.map(event => event.type)).toContain("sigint_received");
      expect(events.map(event => event.type)).toContain("shutdown_complete");
      expect(controller.getState().status).toBe("stopped");
      expect(doneCallback).toHaveBeenCalledTimes(1);
    });
  });
});
