/**
 * Integration test for the Pi Web Bridge
 *
 * Starts a real HTTP+WS server with mock extension API,
 * connects a WS client, sends commands, and verifies responses.
 */

import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { WebSocket } from "ws";

const { createAgentSessionMock } = vi.hoisted(() => ({
  createAgentSessionMock: vi.fn(),
}));

vi.mock("@pi-web/bridge/detached-session", () => ({
  createDetachedAgentSession: createAgentSessionMock,
}));

import type {
  ExtensionAPI,
  ExtensionCommandContext,
} from "@earendil-works/pi-coding-agent";
import { DEFAULT_BRIDGE_CONFIG, type BridgeEvent } from "@pi-web/bridge/types";
import type { WsRpcAdapterContext } from "@pi-web/bridge/ws-rpc-adapter";
import { startBridge, type BridgeController } from "../lifecycle.js";
import {
  createBridgeSessionActions,
  createBridgeSessionEvents,
  createBridgeSessionState,
} from "../pi-live-session.js";
import { createBridgeTerminalView } from "../terminal-log-view.js";

// Test timeout for async operations
const TEST_TIMEOUT = 10000;

describe("Bridge Integration", () => {
  // Create mock Pi extension context
  const createMockContext = (): WsRpcAdapterContext => {
    const sessionManager = {
      getCwd: vi.fn().mockReturnValue("/test/project"),
      getSessionDir: vi.fn().mockReturnValue("/test"),
      getSessionId: vi.fn().mockReturnValue("test-session-123"),
      getSessionFile: vi.fn().mockReturnValue("/test/session.json"),
      getLeafId: vi.fn().mockReturnValue(null),
      getLeafEntry: vi.fn().mockReturnValue(undefined),
      getEntry: vi.fn().mockReturnValue(undefined),
      getLabel: vi.fn().mockReturnValue(undefined),
      getBranch: vi.fn().mockReturnValue([
        { id: "entry-1", role: "user", type: "message", content: "Hello" },
        {
          id: "entry-2",
          role: "assistant",
          type: "message",
          content: "Hi there!",
        },
      ]),
      getHeader: vi.fn().mockReturnValue(null),
      getEntries: vi.fn().mockReturnValue([
        { role: "user", content: "Hello" },
        { role: "assistant", content: "Hi there!" },
      ]),
      getTree: vi.fn().mockReturnValue([]),
      getSessionName: vi.fn().mockReturnValue("Test Session"),
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
      getSessionName: vi.fn().mockReturnValue("Test Session"),
      getCommands: vi
        .fn()
        .mockReturnValue([
          { name: "/test", description: "Test command", source: "extension" },
        ]),
      on: vi.fn(),
    } as unknown as ExtensionAPI;

    const ctx = {
      sessionManager,
      model,
      modelRegistry: {
        getAvailable: vi.fn().mockReturnValue([
          { ...model, id: "model-a", name: "Model A" },
          { ...model, id: "model-b", name: "Model B" },
        ]),
      } as unknown as ExtensionCommandContext["modelRegistry"],
      isIdle: vi.fn().mockReturnValue(true),
      signal: undefined,
      abort: vi.fn(),
      compact: vi.fn(),
      shutdown: vi.fn(),
      hasPendingMessages: vi.fn().mockReturnValue(false),
      getContextUsage: vi.fn().mockReturnValue({
        tokens: 100,
        contextWindow: 1000,
        percent: 10,
      }),
      getSystemPrompt: vi.fn().mockReturnValue("test system prompt"),
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

  // Store original SIGINT listeners
  const originalSigintListeners: Array<NodeJS.SignalsListener> = [];

  let mockContext: WsRpcAdapterContext;
  let controller: BridgeController | undefined;
  let events: BridgeEvent[];

  beforeEach(() => {
    createAgentSessionMock.mockReset();
    mockContext = createMockContext();
    events = [];

    // Capture existing SIGINT listeners
    const listeners = process.listeners("SIGINT");
    originalSigintListeners.length = 0;
    originalSigintListeners.push(...(listeners as any));
    listeners.forEach(l => process.off("SIGINT", l as any));
  });

  afterEach(async () => {
    // Stop controller if running
    if (controller?.getState().status === "running") {
      await controller.stop();
    }
    controller = undefined;

    // Restore original SIGINT listeners
    process.removeAllListeners("SIGINT");
    originalSigintListeners.forEach(l => process.on("SIGINT", l as any));
  });

  describe("Server Lifecycle", () => {
    it(
      "should start server and bind to a port",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const state = controller.getState();
        expect(state.status).toBe("running");
        if (state.status === "running") {
          expect(state.port).toBeGreaterThan(0);
          expect(state.host).toBe(config.host);
        }
      },
      TEST_TIMEOUT,
    );

    it(
      "should publish shutdown lifecycle events to subscribers",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        controller.subscribe(event => events.push(event));
        await controller.stop();

        expect(events.some(e => e.type === "server_stop")).toBe(true);
        expect(events.some(e => e.type === "shutdown_complete")).toBe(true);
      },
      TEST_TIMEOUT,
    );
  });

  describe("WebSocket Client Connection", () => {
    it(
      "should accept WebSocket connections",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;

        // Connect WebSocket client
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        expect(ws.readyState).toBe(WebSocket.OPEN);

        ws.close();
        await new Promise(resolve => setTimeout(resolve, 100));
      },
      TEST_TIMEOUT,
    );

    it(
      "should emit client_connect and client_disconnect events",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        controller.subscribe(event => events.push(event));

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        // Wait for client_connect event
        await new Promise(resolve => setTimeout(resolve, 100));

        ws.close();

        // Wait for client_disconnect event
        await new Promise(resolve => setTimeout(resolve, 100));

        expect(events.some(e => e.type === "client_connect")).toBe(true);
        expect(events.some(e => e.type === "client_disconnect")).toBe(true);
      },
      TEST_TIMEOUT,
    );
  });

  describe("RPC Command Dispatch", () => {
    it(
      "should handle get_state command",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        // Send get_state command
        const commandId = "test-cmd-1";
        const command = {
          type: "command",
          payload: {
            id: commandId,
            type: "get_state",
          },
        };

        const responsePromise = new Promise<unknown>((resolve, reject) => {
          ws.on("message", data => {
            try {
              const msg = JSON.parse(data.toString());
              if (msg.type === "response" && msg.payload?.id === commandId) {
                resolve(msg.payload);
              }
            } catch {
              // Ignore parse errors
            }
          });
          setTimeout(() => reject(new Error("Response timeout")), 5000);
        });

        ws.send(JSON.stringify(command));

        const response = await responsePromise;

        expect(response).toMatchObject({
          type: "response",
          command: "get_state",
          success: true,
          data: {
            sessionId: "test-session-123",
            sessionName: "Hello",
            messageCount: 2,
            pendingMessageCount: 0,
            isStreaming: false,
            steeringMode: "all",
            followUpMode: "all",
          },
        });

        ws.close();
      },
      TEST_TIMEOUT,
    );

    it(
      "should handle get_commands command",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        const commandId = "test-cmd-2";
        const command = {
          type: "command",
          payload: {
            id: commandId,
            type: "get_commands",
          },
        };

        const responsePromise = new Promise<unknown>((resolve, reject) => {
          ws.on("message", data => {
            try {
              const msg = JSON.parse(data.toString());
              if (msg.type === "response" && msg.payload?.id === commandId) {
                resolve(msg.payload);
              }
            } catch {
              // Ignore parse errors
            }
          });
          setTimeout(() => reject(new Error("Response timeout")), 5000);
        });

        ws.send(JSON.stringify(command));

        const response = (await responsePromise) as {
          type: string;
          command: string;
          success: boolean;
          data: { commands: Array<{ name: string }> };
        };

        expect(response.success).toBe(true);
        expect(response.data.commands).toHaveLength(1);
        expect(response.data.commands[0].name).toBe("/test");

        ws.close();
      },
      TEST_TIMEOUT,
    );

    it(
      "should handle prompt command via auto-created session",
      async () => {
        // Set up a real temp directory so SessionManager.create works
        const tmpDir = fs.mkdtempSync(
          path.join(os.tmpdir(), "pi-web-int-prompt-"),
        );
        const sessionFile = path.join(tmpDir, "session.jsonl");
        fs.writeFileSync(
          sessionFile,
          JSON.stringify({
            type: "session",
            version: 3,
            id: "int-test-session",
            timestamp: new Date().toISOString(),
            cwd: tmpDir,
          }),
        );
        (
          mockContext.state.sessionManager.getSessionFile as ReturnType<
            typeof vi.fn
          >
        ).mockReturnValue(sessionFile);
        (mockContext.state as unknown as Record<string, unknown>).cwd = tmpDir;

        // Mock createAgentSession for the auto-created session
        const promptSpy = vi.fn().mockResolvedValue(undefined);
        createAgentSessionMock.mockResolvedValue({
          session: {
            sessionFile: undefined, // set by autoCreateSession
            sessionId: "auto-session",
            isStreaming: false,
            bindExtensions: vi.fn().mockResolvedValue(undefined),
            subscribe: vi.fn().mockReturnValue(() => {}),
            prompt: promptSpy,
            dispose: vi.fn(),
            sessionManager: {
              getSessionFile: vi.fn(),
              getSessionId: vi.fn().mockReturnValue("auto-session"),
              getEntries: vi.fn().mockReturnValue([]),
              getBranch: vi.fn().mockReturnValue([]),
              getCwd: vi.fn().mockReturnValue(tmpDir),
            },
          },
        });

        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        const commandId = "test-cmd-3";
        const command = {
          type: "command",
          payload: {
            id: commandId,
            type: "prompt",
            message: "Hello from bridge",
          },
        };

        const responsePromise = new Promise<unknown>((resolve, reject) => {
          ws.on("message", data => {
            try {
              const msg = JSON.parse(data.toString());
              if (msg.type === "response" && msg.payload?.id === commandId) {
                resolve(msg.payload);
              }
            } catch {
              // Ignore parse errors
            }
          });
          setTimeout(() => reject(new Error("Response timeout")), 5000);
        });

        ws.send(JSON.stringify(command));

        const response = (await responsePromise) as Record<string, unknown>;

        // When no session is selected, prompt auto-creates a detached session.
        // The response command is "new_session" (carrying the new session info)
        // instead of "prompt".
        expect(response).toMatchObject({
          type: "response",
          command: "new_session",
          success: true,
        });
        expect(response.data).toMatchObject({
          cancelled: false,
        });
        expect(
          (response.data as Record<string, unknown>).sessionPath,
        ).toBeDefined();

        // sendUserMessage should NOT be called (that would trigger TUI switch)
        expect(mockContext.actions.sendUserMessage).not.toHaveBeenCalled();

        ws.close();
        fs.rmSync(tmpDir, { recursive: true, force: true });
      },
      TEST_TIMEOUT,
    );

    it(
      "should handle unknown commands with error response",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        controller.subscribe(event => events.push(event));

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        // Wait for client_connect
        await new Promise(resolve => setTimeout(resolve, 100));

        const commandId = "test-cmd-4";
        const command = {
          type: "command",
          payload: {
            id: commandId,
            type: "unknown_command_xyz",
          },
        };

        const responsePromise = new Promise<unknown>((resolve, reject) => {
          ws.on("message", data => {
            try {
              const msg = JSON.parse(data.toString());
              if (msg.type === "response" && msg.payload?.id === commandId) {
                resolve(msg.payload);
              }
            } catch {
              // Ignore parse errors
            }
          });
          setTimeout(() => reject(new Error("Response timeout")), 5000);
        });

        ws.send(JSON.stringify(command));

        const response = (await responsePromise) as {
          success: boolean;
          error?: string;
        };

        expect(response.success).toBe(false);
        expect(response.error).toContain("unknown");

        ws.close();
      },
      TEST_TIMEOUT,
    );
  });

  describe("Terminal Log View Integration", () => {
    it(
      "should create terminal view with bridge events",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const terminalView = createBridgeTerminalView(
          handler => controller!.subscribe(handler),
          () => controller!.getState(),
          () => controller!.getClients(),
          config,
        );

        const renderOutput = terminalView.render();
        expect(renderOutput.length).toBeGreaterThan(0);
        expect(renderOutput.some(line => line.includes("Pi Web Bridge"))).toBe(
          true,
        );
        expect(renderOutput.some(line => line.includes("Bridge:"))).toBe(true);

        terminalView.dispose();
      },
      TEST_TIMEOUT,
    );

    it(
      "should update view when clients connect",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const terminalView = createBridgeTerminalView(
          handler => controller!.subscribe(handler),
          () => controller!.getState(),
          () => controller!.getClients(),
          config,
        );
        const initialRender = terminalView.render();
        expect(initialRender.some(line => line.includes("Clients: 0"))).toBe(
          true,
        );

        // Connect a client
        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        // Wait for event propagation
        await new Promise(resolve => setTimeout(resolve, 100));

        // Re-render - should show client
        const updatedRender = terminalView.render();
        expect(updatedRender.some(line => line.includes("Clients: 1"))).toBe(
          true,
        );

        ws.close();
        terminalView.dispose();
      },
      TEST_TIMEOUT,
    );
  });

  describe("Full Command Flow", () => {
    it(
      "should handle complete request-response cycle with multiple commands",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        // Helper to send command and wait for response
        const sendCommand = async (cmd: unknown): Promise<unknown> => {
          const cmdId = (cmd as { id?: string }).id || crypto.randomUUID();
          const commandWithId = { ...(cmd as object), id: cmdId };

          return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
              reject(new Error(`Timeout waiting for response to ${cmdId}`));
            }, 5000);

            ws.on("message", data => {
              try {
                const msg = JSON.parse(data.toString());
                if (msg.type === "response" && msg.payload?.id === cmdId) {
                  clearTimeout(timeout);
                  resolve(msg.payload);
                }
              } catch {
                // Ignore parse errors
              }
            });

            ws.send(
              JSON.stringify({
                type: "command",
                payload: commandWithId,
              }),
            );
          });
        };

        // Execute multiple commands
        const results = await Promise.all([
          sendCommand({ type: "get_state" }),
          sendCommand({ type: "get_commands" }),
        ]);

        // Verify all succeeded
        for (const result of results) {
          expect((result as { success: boolean }).success).toBe(true);
        }

        // Verify specific data
        const stateResult = results[0] as {
          success: boolean;
          data: { sessionId: string };
        };
        expect(stateResult.data.sessionId).toBe("test-session-123");

        const commandsResult = results[1] as {
          success: boolean;
          data: { commands: Array<{ name: string }> };
        };
        expect(commandsResult.data.commands).toHaveLength(1);

        ws.close();
      },
      TEST_TIMEOUT,
    );
  });

  describe("Error Handling", () => {
    it(
      "should handle malformed JSON messages",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        const responsePromise = new Promise<unknown>((resolve, reject) => {
          ws.on("message", data => {
            try {
              const msg = JSON.parse(data.toString());
              if (msg.type === "response") {
                resolve(msg.payload);
              }
            } catch {
              // Ignore parse errors
            }
          });
          setTimeout(() => reject(new Error("Response timeout")), 5000);
        });

        // Send malformed JSON
        ws.send("this is not valid json{");

        const response = (await responsePromise) as {
          success: boolean;
          error?: string;
        };

        expect(response.success).toBe(false);
        expect(response.error).toContain("parse");

        ws.close();
      },
      TEST_TIMEOUT,
    );

    it(
      "should handle unknown message types",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        const responsePromise = new Promise<unknown>((resolve, reject) => {
          ws.on("message", data => {
            try {
              const msg = JSON.parse(data.toString());
              if (msg.type === "response") {
                resolve(msg.payload);
              }
            } catch {
              // Ignore parse errors
            }
          });
          setTimeout(() => reject(new Error("Response timeout")), 5000);
        });

        // Send unknown message type
        ws.send(
          JSON.stringify({
            type: "unknown_type",
            payload: {},
          }),
        );

        const response = (await responsePromise) as {
          success: boolean;
          error?: string;
        };

        expect(response.success).toBe(false);
        expect(response.error).toContain("Unknown message type");

        ws.close();
      },
      TEST_TIMEOUT,
    );
  });

  describe("Event Broadcast Delivery", () => {
    it(
      "should deliver broadcast events to connected WS clients",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        // Wait for client registration with EventBus
        await new Promise(resolve => setTimeout(resolve, 100));

        // Listen for event messages from the server
        const receivedEvents: unknown[] = [];
        ws.on("message", data => {
          try {
            const msg = JSON.parse(data.toString());
            if (msg.type === "event") {
              receivedEvents.push(msg.payload);
            }
          } catch {
            // Ignore parse errors
          }
        });

        // Get the Pi event handler from subscribe
        const subscribeCalls = (
          mockContext.events.subscribe as ReturnType<typeof vi.fn>
        ).mock.calls;
        const eventHandler = subscribeCalls[subscribeCalls.length - 1]?.[0] as
          | ((event: object) => void)
          | undefined;

        expect(eventHandler).toBeDefined();

        // Trigger a Pi event through the handler
        eventHandler?.({ type: "agent_start", sessionId: "test-session" });

        // Wait for event delivery
        await new Promise(resolve => setTimeout(resolve, 100));

        // Verify the bridge emits the normalized lifecycle payload.
        expect(receivedEvents.length).toBeGreaterThanOrEqual(1);
        expect(receivedEvents[0]).toEqual({
          type: "agent_start",
          sessionPath: "/test/session.json",
        });

        ws.close();
      },
      TEST_TIMEOUT,
    );

    it(
      "should stop delivering events after WS client disconnects",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        // Wait for registration
        await new Promise(resolve => setTimeout(resolve, 100));

        // Disconnect
        ws.close();
        await new Promise(resolve => setTimeout(resolve, 100));

        // Verify client count is 0
        expect(controller!.getClients()).toHaveLength(0);

        // Trigger event — should not throw since client is unregistered
        const subscribeCalls = (
          mockContext.events.subscribe as ReturnType<typeof vi.fn>
        ).mock.calls;
        const eventHandler = subscribeCalls[subscribeCalls.length - 1]?.[0] as
          | ((event: object) => void)
          | undefined;
        expect(eventHandler).toBeDefined();

        // Should not throw — just no-op since client is unregistered
        expect(() => {
          eventHandler?.({ type: "agent_start" });
        }).not.toThrow();
      },
      TEST_TIMEOUT,
    );
  });

  describe("Discovery Commands", () => {
    it(
      "should handle list_sessions command via WebSocket",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        const commandId = "list-sessions-1";
        ws.send(
          JSON.stringify({
            type: "command",
            payload: {
              id: commandId,
              type: "list_sessions",
              workspacePath: "/tmp",
            },
          }),
        );

        const responsePromise = new Promise<unknown>((resolve, reject) => {
          ws.on("message", data => {
            try {
              const msg = JSON.parse(data.toString());
              if (msg.type === "response" && msg.payload?.id === commandId) {
                resolve(msg.payload);
              }
            } catch {
              // Ignore
            }
          });
          setTimeout(() => reject(new Error("Response timeout")), 5000);
        });

        const response = (await responsePromise) as {
          command: string;
          success: boolean;
          data: { sessions: Array<{ id: string; name: string; path: string }> };
        };

        expect(response.command).toBe("list_sessions");
        expect(response.success).toBe(true);
        expect(Array.isArray(response.data.sessions)).toBe(true);

        ws.close();
      },
      TEST_TIMEOUT,
    );

    it(
      "should handle list_tree_entries command via WebSocket",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;
        const ws = new WebSocket(wsUrl);

        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        const commandId = "list-tree-1";
        ws.send(
          JSON.stringify({
            type: "command",
            payload: { id: commandId, type: "list_tree_entries" },
          }),
        );

        const responsePromise = new Promise<unknown>((resolve, reject) => {
          ws.on("message", data => {
            try {
              const msg = JSON.parse(data.toString());
              if (msg.type === "response" && msg.payload?.id === commandId) {
                resolve(msg.payload);
              }
            } catch {
              // Ignore
            }
          });
          setTimeout(() => reject(new Error("Response timeout")), 5000);
        });

        const response = (await responsePromise) as {
          command: string;
          success: boolean;
          data: { entries: Array<{ id: string; label: string; type: string }> };
        };

        expect(response.command).toBe("list_tree_entries");
        expect(response.success).toBe(true);
        expect(response.data.entries).toHaveLength(2);

        ws.close();
      },
      TEST_TIMEOUT,
    );
  });

  describe("SIGINT Handling", () => {
    it(
      "should emit sigint_received and shutdown_complete on SIGINT",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        controller = await startBridge(config, mockContext, vi.fn());

        controller.subscribe(event => events.push(event));

        // Simulate SIGINT
        process.emit("SIGINT");

        // Wait for async shutdown
        await new Promise(resolve => setTimeout(resolve, 200));

        expect(events.some(e => e.type === "sigint_received")).toBe(true);
        expect(events.some(e => e.type === "shutdown_complete")).toBe(true);

        // Controller should be stopped
        expect(controller.getState().status).toBe("stopped");
      },
      TEST_TIMEOUT,
    );
  });

  describe("Lifecycle Events Verification", () => {
    it(
      "should emit all required lifecycle events",
      async () => {
        const config = { ...DEFAULT_BRIDGE_CONFIG, port: 0 };
        const allEvents: BridgeEvent[] = [];

        controller = await startBridge(config, mockContext, vi.fn());
        controller.subscribe(event => allEvents.push(event));

        const address = controller.getState();
        if (address.status !== "running") {
          throw new Error("Bridge not running");
        }

        const wsUrl = `ws://${address.host}:${address.port}/ws`;

        // Connect a client
        const ws = new WebSocket(wsUrl);
        await new Promise<void>((resolve, reject) => {
          ws.on("open", resolve);
          ws.on("error", reject);
          setTimeout(() => reject(new Error("Connection timeout")), 5000);
        });

        // Wait for client_connect event
        await new Promise(resolve => setTimeout(resolve, 100));

        // Send a command that will succeed
        ws.send(
          JSON.stringify({
            type: "command",
            payload: { type: "get_state" },
          }),
        );

        // Wait for command_received event
        await new Promise(resolve => setTimeout(resolve, 100));

        // Note: command_error is only emitted on dispatch exceptions, not for
        // commands that return error responses (like unsupported commands)
        // To trigger command_error, we would need a command that throws during dispatch

        // Disconnect client
        ws.close();
        await new Promise(resolve => setTimeout(resolve, 100));

        // Stop the bridge
        await controller.stop();

        // Verify all lifecycle events were emitted
        const eventTypes = allEvents.map(e => e.type);

        // Required events per slice verification:
        // - server_stop
        // - client_connect
        // - client_disconnect
        // - command_received
        // - shutdown_complete
        // Note: command_error is only emitted on dispatch exceptions

        expect(eventTypes).toContain("server_stop");
        expect(eventTypes).toContain("client_connect");
        expect(eventTypes).toContain("client_disconnect");
        expect(eventTypes).toContain("command_received");
        expect(eventTypes).toContain("shutdown_complete");
      },
      TEST_TIMEOUT,
    );
  });
});
