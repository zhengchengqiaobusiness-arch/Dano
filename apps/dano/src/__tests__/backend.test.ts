import type {
  AgentSession,
  AgentSessionEvent,
} from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";
import { DEFAULT_BRIDGE_CONFIG } from "../bridge/types.js";
import { createDanoBackendFromSession } from "../backend.js";
import { startDanoServer } from "../server.js";

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
    appendModelChange: vi.fn(),
    appendThinkingLevelChange: vi.fn(),
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
      .mockReturnValue([
        {
          name: "deploy",
          invocationName: "deploy:1",
          description: "First deploy command",
        },
        {
          name: "deploy",
          invocationName: "deploy:2",
          description: "Second deploy command",
        },
        {
          name: "template",
          invocationName: "template",
          description: "Extension wins callable-name collisions",
        },
      ]),
  };

  const session = {
    sessionManager,
    modelRegistry,
    settingsManager: {
      getDefaultProvider: vi.fn().mockReturnValue("openai"),
      getDefaultModel: vi.fn().mockReturnValue("gpt-4"),
      getDefaultThinkingLevel: vi.fn().mockReturnValue("medium"),
    },
    extensionRunner,
    promptTemplates: [
      { name: "template", description: "Shadowed prompt template" },
      { name: "review", description: "Review prompt template" },
    ],
    resourceLoader: {
      getSkills: vi.fn().mockReturnValue({
        skills: [
          { name: "audit", description: "Audit with the project skill" },
        ],
        diagnostics: [],
      }),
    },
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

describe("Dano backend", () => {
  it("adapts an AgentSession into bridge state, actions, and events", async () => {
    const mock = createMockSession();
    const backend = createDanoBackendFromSession(mock.session);
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
      {
        name: "deploy:1",
        description: "First deploy command",
        source: "extension",
      },
      {
        name: "deploy:2",
        description: "Second deploy command",
        source: "extension",
      },
      {
        name: "review",
        description: "Review prompt template",
        source: "prompt",
      },
      {
        name: "skill:audit",
        description: "Audit with the project skill",
        source: "skill",
      },
      {
        name: "template",
        description: "Extension wins callable-name collisions",
        source: "extension",
      },
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

  it("hydrates a missing Dano current model and writes it to the session", () => {
    const model = {
      id: "gpt-4",
      name: "GPT-4",
      provider: "openai",
      api: "openai-responses",
      reasoning: true,
      contextWindow: 128000,
      maxTokens: 8192,
    };
    const branchEntries: unknown[] = [];
    const sessionState: { model?: typeof model } = {};
    const sessionManager = {
      getCwd: vi.fn().mockReturnValue("/test/project"),
      getSessionId: vi.fn().mockReturnValue("session-456"),
      getSessionFile: vi.fn().mockReturnValue("/test/session-456.jsonl"),
      getBranch: vi.fn(() => branchEntries),
      appendModelChange: vi.fn((provider: string, modelId: string) => {
        branchEntries.push({
          type: "model_change",
          provider,
          modelId,
        });
      }),
      appendThinkingLevelChange: vi.fn((thinkingLevel: string) => {
        branchEntries.push({
          type: "thinking_level_change",
          thinkingLevel,
        });
      }),
    };
    const session = {
      sessionManager,
      modelRegistry: {
        getAvailable: vi.fn().mockReturnValue([model]),
      },
      settingsManager: {
        getDefaultProvider: vi.fn().mockReturnValue("openai"),
        getDefaultModel: vi.fn().mockReturnValue("gpt-4"),
        getDefaultThinkingLevel: vi.fn().mockReturnValue("medium"),
      },
      extensionRunner: {
        getRegisteredCommands: vi.fn().mockReturnValue([]),
      },
      promptTemplates: [],
      state: sessionState,
      get model() {
        return sessionState.model;
      },
      thinkingLevel: "medium",
      isStreaming: false,
      getContextUsage: vi.fn().mockReturnValue(null),
      subscribe: vi.fn().mockReturnValue(vi.fn()),
      sendUserMessage: vi.fn(),
      abort: vi.fn(),
      setModel: vi.fn(),
      setThinkingLevel: vi.fn(),
      setSessionName: vi.fn(),
      dispose: vi.fn(),
    };

    const backend = createDanoBackendFromSession(
      session as unknown as AgentSession,
    );

    expect(backend.context.state.getCurrentModel()).toMatchObject({
      provider: "openai",
      id: "gpt-4",
    });
    expect(session.model).toMatchObject({
      provider: "openai",
      id: "gpt-4",
    });
    expect(sessionManager.appendModelChange).toHaveBeenCalledWith(
      "openai",
      "gpt-4",
    );
  });

  it("uses Dano config before Pi settings when hydrating Dano state", () => {
    const xiaomiModel = {
      id: "mimo-v2.5",
      name: "MiMo V2.5",
      provider: "xiaomi-token-plan-cn",
      api: "openai-responses",
      reasoning: true,
      contextWindow: 128000,
      maxTokens: 8192,
    };
    const openaiModel = {
      ...xiaomiModel,
      id: "gpt-4",
      name: "GPT-4",
      provider: "openai",
    };
    const branchEntries: unknown[] = [];
    const sessionState: { model?: typeof xiaomiModel; thinkingLevel: string } = {
      thinkingLevel: "off",
    };
    const sessionManager = {
      getCwd: vi.fn().mockReturnValue("/test/project"),
      getSessionId: vi.fn().mockReturnValue("session-dano"),
      getSessionFile: vi.fn().mockReturnValue("/test/session-dano.jsonl"),
      getBranch: vi.fn(() => branchEntries),
      appendModelChange: vi.fn((provider: string, modelId: string) => {
        branchEntries.push({
          type: "model_change",
          provider,
          modelId,
        });
      }),
      appendThinkingLevelChange: vi.fn((thinkingLevel: string) => {
        branchEntries.push({
          type: "thinking_level_change",
          thinkingLevel,
        });
      }),
    };
    const session = {
      sessionManager,
      modelRegistry: {
        getAvailable: vi.fn().mockReturnValue([openaiModel, xiaomiModel]),
      },
      settingsManager: {
        getDefaultProvider: vi.fn().mockReturnValue("openai"),
        getDefaultModel: vi.fn().mockReturnValue("gpt-4"),
        getDefaultThinkingLevel: vi.fn().mockReturnValue("high"),
      },
      extensionRunner: {
        getRegisteredCommands: vi.fn().mockReturnValue([]),
      },
      promptTemplates: [],
      state: sessionState,
      get model() {
        return sessionState.model;
      },
      get thinkingLevel() {
        return sessionState.thinkingLevel;
      },
      isStreaming: false,
      getContextUsage: vi.fn().mockReturnValue(null),
      subscribe: vi.fn().mockReturnValue(vi.fn()),
      sendUserMessage: vi.fn(),
      abort: vi.fn(),
      setModel: vi.fn(),
      setThinkingLevel: vi.fn(),
      setSessionName: vi.fn(),
      dispose: vi.fn(),
    };

    const backend = createDanoBackendFromSession(
      session as unknown as AgentSession,
      {
        defaultProvider: "xiaomi-token-plan-cn",
        defaultModel: "mimo-v2.5",
        defaultThinkingLevel: "medium",
      },
    );

    expect(backend.context.state.getCurrentModel()).toMatchObject({
      provider: "xiaomi-token-plan-cn",
      id: "mimo-v2.5",
    });
    expect(backend.context.state.getThinkingLevel()).toBe("medium");
    expect(sessionManager.appendModelChange).toHaveBeenCalledWith(
      "xiaomi-token-plan-cn",
      "mimo-v2.5",
    );
    expect(sessionManager.appendThinkingLevelChange).toHaveBeenCalledWith(
      "medium",
    );
  });

  it("falls back to Pi settings when Dano config is missing model fields", () => {
    const model = {
      id: "gpt-4",
      name: "GPT-4",
      provider: "openai",
      api: "openai-responses",
      reasoning: true,
      contextWindow: 128000,
      maxTokens: 8192,
    };
    const branchEntries: unknown[] = [];
    const sessionState: { model?: typeof model; thinkingLevel: string } = {
      thinkingLevel: "off",
    };
    const sessionManager = {
      getCwd: vi.fn().mockReturnValue("/test/project"),
      getSessionId: vi.fn().mockReturnValue("session-fallback"),
      getSessionFile: vi.fn().mockReturnValue("/test/session-fallback.jsonl"),
      getBranch: vi.fn(() => branchEntries),
      appendModelChange: vi.fn((provider: string, modelId: string) => {
        branchEntries.push({
          type: "model_change",
          provider,
          modelId,
        });
      }),
      appendThinkingLevelChange: vi.fn((thinkingLevel: string) => {
        branchEntries.push({
          type: "thinking_level_change",
          thinkingLevel,
        });
      }),
    };
    const session = {
      sessionManager,
      modelRegistry: {
        getAvailable: vi.fn().mockReturnValue([model]),
      },
      settingsManager: {
        getDefaultProvider: vi.fn().mockReturnValue("openai"),
        getDefaultModel: vi.fn().mockReturnValue("gpt-4"),
        getDefaultThinkingLevel: vi.fn().mockReturnValue("high"),
      },
      extensionRunner: {
        getRegisteredCommands: vi.fn().mockReturnValue([]),
      },
      promptTemplates: [],
      state: sessionState,
      get model() {
        return sessionState.model;
      },
      get thinkingLevel() {
        return sessionState.thinkingLevel;
      },
      isStreaming: false,
      getContextUsage: vi.fn().mockReturnValue(null),
      subscribe: vi.fn().mockReturnValue(vi.fn()),
      sendUserMessage: vi.fn(),
      abort: vi.fn(),
      setModel: vi.fn(),
      setThinkingLevel: vi.fn(),
      setSessionName: vi.fn(),
      dispose: vi.fn(),
    };

    const backend = createDanoBackendFromSession(
      session as unknown as AgentSession,
      {
        defaultProvider: "xiaomi-token-plan-cn",
        defaultThinkingLevel: "medium",
      },
    );

    expect(backend.context.state.getCurrentModel()).toMatchObject({
      provider: "openai",
      id: "gpt-4",
    });
    expect(backend.context.state.getThinkingLevel()).toBe("medium");
    expect(sessionManager.appendModelChange).toHaveBeenCalledWith(
      "openai",
      "gpt-4",
    );
  });

  it("starts and stops the Dano server lifecycle", async () => {
    const mock = createMockSession();
    const backend = createDanoBackendFromSession(mock.session);
    const controller = await startDanoServer(
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
    const backend = createDanoBackendFromSession(mock.session);

    const first = await startDanoServer(
      { ...DEFAULT_BRIDGE_CONFIG, port: 0 },
      {
        backend,
        captureSigint: false,
      },
    );
    await first.stop();

    const second = await startDanoServer(
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
