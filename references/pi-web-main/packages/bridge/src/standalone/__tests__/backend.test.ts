import type {
  AgentSession,
  AgentSessionEvent,
} from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";
import { DEFAULT_BRIDGE_CONFIG } from "../../types.js";
import { createStandaloneBridgeContextFromSession } from "../backend.js";
import { startStandaloneBridge } from "../server.js";

function createMockSession() {
  let eventHandler: ((event: AgentSessionEvent) => void) | undefined;
  const unsubscribe = vi.fn();

  const sessionManager = {
    getCwd: vi.fn().mockReturnValue("/test/project"),
    getSessionDir: vi.fn().mockReturnValue("/test"),
    getSessionId: vi.fn().mockReturnValue("session-123"),
    getSessionFile: vi.fn().mockReturnValue("/test/session.jsonl"),
    getLeafId: vi.fn().mockReturnValue(null),
    getLeafEntry: vi.fn().mockReturnValue(undefined),
    getEntry: vi.fn().mockReturnValue(undefined),
    getLabel: vi.fn().mockReturnValue(undefined),
    getBranch: vi.fn().mockReturnValue([{ role: "user", content: "Hello" }]),
    getHeader: vi.fn().mockReturnValue(null),
    getEntries: vi.fn().mockReturnValue([{ role: "user", content: "Hello" }]),
    getTree: vi.fn().mockReturnValue([]),
    getSessionName: vi.fn().mockReturnValue("test-session"),
  };

  const modelRegistry = {
    getAvailable: vi.fn().mockReturnValue([
      {
        id: "gpt-4",
        name: "GPT-4",
        provider: "openai",
        api: "openai-responses",
        reasoning: true,
        contextWindow: 128000,
        maxTokens: 8192,
      },
    ]),
  };

  const extensionRunner = {
    getRegisteredCommands: vi
      .fn()
      .mockReturnValue([{ name: "/ext", description: "Extension command" }]),
  };

  const session = {
    sessionManager,
    modelRegistry,
    extensionRunner,
    promptTemplates: [{ name: "template", description: "Prompt template" }],
    model: {
      id: "gpt-4",
      name: "GPT-4",
      provider: "openai",
      api: "openai-responses",
      reasoning: true,
      contextWindow: 128000,
      maxTokens: 8192,
    },
    thinkingLevel: "medium",
    isStreaming: false,
    getContextUsage: vi
      .fn()
      .mockReturnValue({ tokens: 1200, contextWindow: 8000, percent: 15 }),
    subscribe: vi.fn((handler: (event: AgentSessionEvent) => void) => {
      eventHandler = handler;
      return unsubscribe;
    }),
    sendUserMessage: vi.fn().mockResolvedValue(undefined),
    abort: vi.fn().mockResolvedValue(undefined),
    setModel: vi.fn(async model => {
      session.model = {
        ...session.model,
        ...model,
      };
    }),
    setThinkingLevel: vi.fn(level => {
      session.thinkingLevel = level;
    }),
    setSessionName: vi.fn(),
    dispose: vi.fn(),
  };

  return {
    session: session as unknown as AgentSession,
    emit(event: AgentSessionEvent) {
      eventHandler?.(event);
    },
    unsubscribe,
  };
}

describe("standalone bridge backend", () => {
  it("adapts an AgentSession into bridge state, actions, and events", async () => {
    const mock = createMockSession();
    const backend = createStandaloneBridgeContextFromSession(mock.session);
    const received: string[] = [];

    backend.context.events.subscribe(event => {
      received.push(event.type);
    });

    expect(backend.context.state.cwd).toBe("/test/project");
    expect(backend.context.state.isIdle()).toBe(true);
    expect(backend.context.state.hasPendingMessages()).toBe(false);
    expect(backend.context.state.getThinkingLevel()).toBe("medium");
    expect(backend.context.state.getCurrentModel()?.id).toBe("gpt-4");
    expect(backend.context.state.getContextUsage()).toEqual({
      tokens: 1200,
      contextWindow: 8000,
      percent: 15,
    });
    expect(backend.context.actions.getCommands()).toEqual([
      { name: "/ext", description: "Extension command" },
      { name: "/template", description: "Prompt template" },
    ]);

    mock.emit({ type: "agent_start" });
    mock.emit({
      type: "queue_update",
      steering: ["one"],
      followUp: [],
    });
    mock.emit({
      type: "message_start",
      message: { role: "assistant", content: [] },
    } as unknown as AgentSessionEvent);
    mock.emit({
      type: "compaction_end",
      reason: "manual",
      result: undefined,
      aborted: false,
      willRetry: false,
    });

    expect(received).toEqual([
      "agent_start",
      "message_start",
      "session_compact",
    ]);
    expect(backend.context.state.hasPendingMessages()).toBe(true);

    backend.context.actions.sendUserMessage("hello", { deliverAs: "followUp" });
    expect(mock.session.sendUserMessage).toHaveBeenCalledWith("hello", {
      deliverAs: "followUp",
    });

    backend.context.actions.abort();
    expect(mock.session.abort).toHaveBeenCalled();

    await backend.context.actions.setModel({
      id: "claude",
      provider: "anthropic",
    });
    expect(mock.session.setModel).toHaveBeenCalledWith({
      id: "claude",
      provider: "anthropic",
    });
    expect(received.at(-1)).toBe("model_select");

    backend.context.actions.setThinkingLevel("high");
    expect(mock.session.setThinkingLevel).toHaveBeenCalledWith("high");

    backend.context.actions.setSessionName("renamed");
    expect(mock.session.setSessionName).toHaveBeenCalledWith("renamed");

    await backend.dispose();
    expect(mock.unsubscribe).toHaveBeenCalled();
    expect(mock.session.dispose).toHaveBeenCalled();
  });

  it("starts and stops the standalone server lifecycle", async () => {
    const mock = createMockSession();
    const backend = createStandaloneBridgeContextFromSession(mock.session);
    const controller = await startStandaloneBridge(
      { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
      {
        backend,
        captureSigint: false,
      },
    );

    expect(controller.getState().status).toBe("running");
    expect(controller.getBridgeUrl()).toMatch(/^http:\/\//);
    expect(controller.getClients()).toEqual([]);

    await controller.stop();

    expect(controller.getState()).toEqual({ status: "stopped" });
    expect(mock.session.dispose).not.toHaveBeenCalled();
  });

  it("reuses a provided backend across bridge restarts", async () => {
    const mock = createMockSession();
    const backend = createStandaloneBridgeContextFromSession(mock.session);

    const first = await startStandaloneBridge(
      { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
      {
        backend,
        captureSigint: false,
      },
    );
    await first.stop();

    const second = await startStandaloneBridge(
      { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
      {
        backend,
        captureSigint: false,
      },
    );

    expect(second.getState().status).toBe("running");
    expect(mock.session.dispose).not.toHaveBeenCalled();

    await second.stop();
    await backend.dispose();

    expect(mock.session.dispose).toHaveBeenCalledTimes(1);
  });
});
