import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";
import {
  fauxAssistantMessage,
  fauxProvider,
  type Context,
  type FauxResponseStep,
} from "@earendil-works/pi-ai";
import {
  ModelRuntime,
  SessionManager,
  SettingsManager,
  createAgentSession,
} from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";
import { createDanoBackendFromSession } from "../backend.js";
import {
  createFieldAssistService,
  createPiSdkFieldAssistClient,
} from "../bridge/field-assist.js";
import {
  DEFAULT_BRIDGE_CONFIG,
  type ClientMessage,
  type ServerMessage,
} from "../bridge/types.js";
import { startDanoServer } from "../server.js";

function waitForSseMessage(
  url: string,
  predicate: (message: ServerMessage) => boolean,
  label = "expected SSE message",
): { close(): void; ready: Promise<void>; result: Promise<ServerMessage> } {
  let request: http.ClientRequest;
  let markReady: () => void;
  const ready = new Promise<void>(resolve => {
    markReady = resolve;
  });
  const result = new Promise<ServerMessage>((resolve, reject) => {
    let buffer = "";
    const timeout = setTimeout(() => {
      request.destroy();
      reject(new Error(`Timed out waiting for ${label}`));
    }, 2_000);

    request = http.get(url, response => {
      markReady();
      response.setEncoding("utf8");
      response.on("data", chunk => {
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
            const message = JSON.parse(data) as ServerMessage;
            if (predicate(message)) {
              clearTimeout(timeout);
              resolve(message);
              return;
            }
          }
          boundary = buffer.indexOf("\n\n");
        }
      });
      response.on("error", reject);
    });
    request.on("error", reject);
  });

  return {
    ready,
    close() {
      request.destroy();
    },
    result,
  };
}

async function createFieldAssistHttpHarness(
  responses: FauxResponseStep[],
  options: {
    settingsManager?: SettingsManager;
    timeoutMs?: number;
    tokensPerSecond?: number;
  } = {},
) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-field-assist-http-"));
  const provider = fauxProvider({
    provider: "dano-field-assist-http",
    tokensPerSecond: options.tokensPerSecond,
  });
  provider.setResponses(responses);
  const modelRuntime = await ModelRuntime.create({
    authPath: path.join(root, "agent", "auth.json"),
    modelsPath: null,
  });
  modelRuntime.registerNativeProvider(provider.provider);
  await modelRuntime.setRuntimeApiKey(provider.provider.id, "test-only");
  const { session } = await createAgentSession({
    cwd: root,
    agentDir: path.join(root, "agent"),
    modelRuntime,
    model: provider.getModel(),
    thinkingLevel: "off",
    noTools: "all",
    sessionManager: SessionManager.inMemory(root),
    settingsManager: options.settingsManager,
  });
  const backend = createDanoBackendFromSession(session);
  if (options.timeoutMs !== undefined) {
    backend.context.fieldAssist = createFieldAssistService({
      ai: createPiSdkFieldAssistClient({ modelRuntime }),
      getCurrentModel: () => session.model,
      timeoutMs: options.timeoutMs,
    });
  }
  const controller = await startDanoServer(
    {
      ...DEFAULT_BRIDGE_CONFIG,
      host: "127.0.0.1",
      port: 0,
      upload: {
        ...DEFAULT_BRIDGE_CONFIG.upload,
        uploadDir: path.join(root, "uploads"),
      },
    },
    { backend, captureSigint: false },
  );
  const origin = controller.getBridgeUrl();
  if (!origin) throw new Error("Dano Field Assist test server did not start");

  return {
    provider,
    session,
    async execute(command: ClientMessage): Promise<ServerMessage> {
      const createResponse = await fetch(`${origin}/api/clients`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const created = (await createResponse.json()) as {
        eventsUrl: string;
        messagesUrl: string;
      };
      const correlationId =
        command.type === "command" ? command.payload.id : undefined;
      const sse = waitForSseMessage(
        `${origin}${created.eventsUrl}`,
        message =>
          message.type === "response" &&
          message.payload.id === correlationId,
        `Field Assist response ${correlationId}`,
      );
      try {
        await sse.ready;
        const response = await fetch(`${origin}${created.messagesUrl}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(command),
        });
        expect(response.status).toBe(202);
        return await sse.result;
      } finally {
        sse.close();
      }
    },
    async dispose() {
      await controller.stop();
      await backend.dispose();
      fs.rmSync(root, { recursive: true, force: true });
    },
  };
}

describe("Pi 0.85.1 HTTP/SSE regression baseline", () => {
  it("projects a real Pi runtime configured through package-root interfaces", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-pi-http-sse-"));
    const provider = fauxProvider({ provider: "dano-regression" });
    provider.setResponses([fauxAssistantMessage("baseline response")]);
    const modelRuntime = await ModelRuntime.create({
      authPath: path.join(root, "agent", "auth.json"),
      modelsPath: null,
    });
    modelRuntime.registerNativeProvider(provider.provider);
    await modelRuntime.getAvailable("dano-regression");

    const { session } = await createAgentSession({
      cwd: root,
      agentDir: path.join(root, "agent"),
      modelRuntime,
      model: provider.getModel(),
      thinkingLevel: "off",
      noTools: "all",
      sessionManager: SessionManager.inMemory(root),
    });
    const backend = createDanoBackendFromSession(session);
    const controller = await startDanoServer(
      {
        ...DEFAULT_BRIDGE_CONFIG,
        host: "127.0.0.1",
        port: 0,
        upload: {
          ...DEFAULT_BRIDGE_CONFIG.upload,
          uploadDir: path.join(root, "uploads"),
        },
      },
      { backend, captureSigint: false },
    );

    let sse: ReturnType<typeof waitForSseMessage> | undefined;
    try {
      const origin = controller.getBridgeUrl();
      if (!origin) throw new Error("Dano test server did not start");
      const createResponse = await fetch(`${origin}/api/clients`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      expect(createResponse.status).toBe(201);
      const created = (await createResponse.json()) as {
        eventsUrl: string;
        messagesUrl: string;
      };
      sse = waitForSseMessage(
        `${origin}${created.eventsUrl}`,
        message =>
          message.type === "response" &&
          message.payload.id === "pi-baseline-models",
      );

      const command: ClientMessage = {
        type: "command",
        payload: { id: "pi-baseline-models", type: "get_available_models" },
      };
      const postResponse = await fetch(`${origin}${created.messagesUrl}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(command),
      });
      expect(postResponse.status).toBe(202);
      const response = await sse.result;

      expect(response).toMatchObject({
        type: "response",
        payload: {
          id: "pi-baseline-models",
          command: "get_available_models",
          success: true,
        },
      });
      const models = (
        response.payload as {
          data?: { models?: Array<{ provider: string; id: string }> };
        }
      ).data?.models;
      expect(models).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            provider: "dano-regression",
            id: provider.getModel().id,
          }),
        ]),
      );
    } finally {
      sse?.close();
      await controller.stop();
      await backend.dispose();
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("runs Field Assist through ModelRuntime without changing the main Assistant Turn", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-field-assist-"));
    const provider = fauxProvider({ provider: "dano-field-assist" });
    let observedContext: Context | undefined;
    provider.setResponses([
      context => {
        observedContext = context;
        return fauxAssistantMessage("个人事务需要处理。");
      },
    ]);
    const modelRuntime = await ModelRuntime.create({
      authPath: path.join(root, "agent", "auth.json"),
      modelsPath: null,
    });
    modelRuntime.registerNativeProvider(provider.provider);
    await modelRuntime.setRuntimeApiKey("dano-field-assist", "test-only");
    await modelRuntime.getAvailable("dano-field-assist");
    const sessionManager = SessionManager.create(root, root);
    const { session } = await createAgentSession({
      cwd: root,
      agentDir: path.join(root, "agent"),
      modelRuntime,
      model: provider.getModel(),
      thinkingLevel: "off",
      noTools: "all",
      sessionManager,
    });
    sessionManager.appendMessage({
      role: "user",
      content: "主会话已有内容",
      timestamp: Date.now(),
    });
    sessionManager.appendMessage(fauxAssistantMessage("主会话已有回答"));
    const sessionFile = sessionManager.getSessionFile();
    if (!sessionFile) throw new Error("Expected a persistent main session file");
    const sessionFileBefore = fs.readFileSync(sessionFile, "utf8");
    const sessionFilesBefore = fs.readdirSync(root)
      .filter(name => name.endsWith(".jsonl"))
      .sort();
    const entriesBefore = structuredClone(sessionManager.getEntries());
    const mainSessionEvents: string[] = [];
    const unsubscribeMainSession = session.subscribe(event => {
      mainSessionEvents.push(event.type);
    });
    const backend = createDanoBackendFromSession(session);
    const controller = await startDanoServer(
      {
        ...DEFAULT_BRIDGE_CONFIG,
        host: "127.0.0.1",
        port: 0,
        upload: {
          ...DEFAULT_BRIDGE_CONFIG.upload,
          uploadDir: path.join(root, "uploads"),
        },
      },
      { backend, captureSigint: false },
    );

    let sse: ReturnType<typeof waitForSseMessage> | undefined;
    try {
      const origin = controller.getBridgeUrl();
      if (!origin) throw new Error("Dano test server did not start");
      const createResponse = await fetch(`${origin}/api/clients`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const created = (await createResponse.json()) as {
        eventsUrl: string;
        messagesUrl: string;
      };
      sse = waitForSseMessage(
        `${origin}${created.eventsUrl}`,
        message =>
          message.type === "response" &&
          message.payload.id === "field-assist-model-runtime",
        "Field Assist response",
      );
      await sse.ready;

      const command: ClientMessage = {
        type: "command",
        payload: {
          id: "field-assist-model-runtime",
          type: "field_assist",
          requestId: "leave-form:reason",
          action: "polish",
          fieldType: "textarea",
          requestMethod: "editor",
          title: "请假原因",
          currentValue: "个人事务",
        },
      };
      const postResponse = await fetch(`${origin}${created.messagesUrl}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(command),
      });
      expect(postResponse.status).toBe(202);
      await expect(sse.result).resolves.toMatchObject({
        type: "response",
        payload: {
          id: "field-assist-model-runtime",
          command: "field_assist",
          success: true,
          data: {
            value: "个人事务需要处理。",
            metadata: {
              model: {
                provider: "dano-field-assist",
                id: provider.getModel().id,
              },
            },
          },
        },
      });

      expect(provider.state.callCount).toBe(1);
      expect(observedContext?.systemPrompt).toContain("字段文本润色助手");
      expect(observedContext?.messages).toEqual([
        expect.objectContaining({
          role: "user",
          content: "个人事务",
        }),
      ]);
      expect(observedContext?.tools).toBeUndefined();
      expect(sessionManager.getEntries()).toEqual(entriesBefore);
      expect(fs.readFileSync(sessionFile, "utf8")).toBe(sessionFileBefore);
      expect(
        fs.readdirSync(root).filter(name => name.endsWith(".jsonl")).sort(),
      ).toEqual(sessionFilesBefore);
      expect(mainSessionEvents).toEqual([]);
      expect(session.pendingMessageCount).toBe(0);
      expect(session.isStreaming).toBe(false);
      expect(session.model).toMatchObject({
        provider: "dano-field-assist",
        id: provider.getModel().id,
      });
      expect(session.thinkingLevel).toBe("off");
    } finally {
      unsubscribeMainSession();
      sse?.close();
      await controller.stop();
      await backend.dispose();
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("preserves main Assistant Turn auto-retry after Field Assist", async () => {
    const settingsManager = SettingsManager.inMemory({
      retry: {
        enabled: true,
        maxRetries: 1,
        baseDelayMs: 0,
        provider: { maxRetries: 0 },
      },
    });
    const harness = await createFieldAssistHttpHarness(
      [fauxAssistantMessage("个人事务需要处理。")],
      { settingsManager },
    );
    const sessionEvents: string[] = [];
    const unsubscribe = harness.session.subscribe(event => {
      sessionEvents.push(event.type);
    });

    try {
      await expect(
        harness.execute({
          type: "command",
          payload: {
            id: "field-assist-before-main-retry",
            type: "field_assist",
            requestId: "leave-form:reason",
            action: "polish",
            fieldType: "textarea",
            requestMethod: "editor",
            title: "请假原因",
            currentValue: "个人事务",
          },
        }),
      ).resolves.toMatchObject({
        payload: {
          id: "field-assist-before-main-retry",
          success: true,
          data: { value: "个人事务需要处理。" },
        },
      });
      expect(sessionEvents).toEqual([]);

      harness.provider.setResponses([
        fauxAssistantMessage([], {
          stopReason: "error",
          errorMessage: "429 rate limit exceeded",
        }),
        fauxAssistantMessage("主会话自动重试成功。"),
      ]);
      await harness.session.prompt("验证主会话自动重试");

      expect(harness.provider.state.callCount).toBe(3);
      expect(sessionEvents).toContain("auto_retry_start");
      expect(sessionEvents).toContain("auto_retry_end");
      expect(harness.session.messages.at(-1)).toMatchObject({
        role: "assistant",
        content: [
          expect.objectContaining({
            type: "text",
            text: "主会话自动重试成功。",
          }),
        ],
      });
    } finally {
      unsubscribe();
      await harness.dispose();
    }
  });

  it("preserves Field Assist error projection at the HTTP/SSE seam", async () => {
    const invalidFollowUp = fauxAssistantMessage("请补充一下具体内容");
    const harness = await createFieldAssistHttpHarness([
      fauxAssistantMessage([], {
        stopReason: "error",
        errorMessage: "provider failed",
      }),
      ...Array.from({ length: 11 }, () => invalidFollowUp),
    ]);
    const command = (
      id: string,
      currentValue: string,
    ): ClientMessage => ({
      type: "command",
      payload: {
        id,
        type: "field_assist",
        requestId: `field:${id}`,
        action: "polish",
        fieldType: "textarea",
        requestMethod: "editor",
        title: "说明",
        currentValue,
      },
    });

    try {
      await expect(
        harness.execute(command("provider", "失败内容")),
      ).resolves.toMatchObject({
        payload: {
          id: "provider",
          command: "field_assist",
          success: false,
          error: "AI assist returned empty content",
        },
      });
      await expect(
        harness.execute(command("invalid", "无效内容")),
      ).resolves.toMatchObject({
        payload: {
          id: "invalid",
          command: "field_assist",
          success: false,
          error: "AI 辅助返回了追问内容，请重试",
        },
      });
      expect(harness.provider.state.callCount).toBe(12);
    } finally {
      await harness.dispose();
    }

    const timeoutHarness = await createFieldAssistHttpHarness(
      [fauxAssistantMessage("这个响应会在超时后才完成")],
      { timeoutMs: 1, tokensPerSecond: 100 },
    );
    try {
      await expect(
        timeoutHarness.execute(command("timeout", "超时内容")),
      ).resolves.toMatchObject({
        payload: {
          id: "timeout",
          command: "field_assist",
          success: false,
          error: "AI assist timed out",
        },
      });
      expect(timeoutHarness.provider.state.callCount).toBe(1);
    } finally {
      await timeoutHarness.dispose();
    }
  });

  it("keeps concurrent HTTP/SSE Field Assist responses isolated on one ModelRuntime", async () => {
    const respondByField: FauxResponseStep = async context => {
      const value = context.messages.at(-1)?.content;
      const serialized =
        typeof value === "string" ? value : JSON.stringify(value);
      if (serialized.includes("并发甲")) {
        await new Promise(resolve => setTimeout(resolve, 20));
        return fauxAssistantMessage("并发甲润色结果");
      }
      return fauxAssistantMessage("并发乙润色结果");
    };
    const harness = await createFieldAssistHttpHarness([
      respondByField,
      respondByField,
    ]);
    const command = (id: string, currentValue: string): ClientMessage => ({
      type: "command",
      payload: {
        id,
        type: "field_assist",
        requestId: `field:${id}`,
        action: "polish",
        fieldType: "textarea",
        requestMethod: "editor",
        title: id,
        currentValue,
      },
    });

    try {
      const [first, second] = await Promise.all([
        harness.execute(command("concurrent-a", "并发甲")),
        harness.execute(command("concurrent-b", "并发乙")),
      ]);
      expect(first).toMatchObject({
        payload: {
          id: "concurrent-a",
          success: true,
          data: { value: "并发甲润色结果" },
        },
      });
      expect(second).toMatchObject({
        payload: {
          id: "concurrent-b",
          success: true,
          data: { value: "并发乙润色结果" },
        },
      });
      expect(harness.provider.state.callCount).toBe(2);
    } finally {
      await harness.dispose();
    }
  });

  it("projects Pi settings for a runtime-backed new session", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-pi-settings-"));
    const provider = fauxProvider({
      provider: "dano-settings",
      models: [{ id: "configured", reasoning: true }],
    });
    const modelRuntime = await ModelRuntime.create({
      authPath: path.join(root, "agent", "auth.json"),
      modelsPath: null,
    });
    modelRuntime.registerNativeProvider(provider.provider);
    await modelRuntime.setRuntimeApiKey("dano-settings", "test-only");
    await modelRuntime.getAvailable("dano-settings");
    const settingsManager = SettingsManager.inMemory({
      defaultProvider: "dano-settings",
      defaultModel: "configured",
      defaultThinkingLevel: "high",
    });
    const { session } = await createAgentSession({
      cwd: root,
      agentDir: path.join(root, "agent"),
      modelRuntime,
      settingsManager,
      noTools: "all",
      sessionManager: SessionManager.create(root, root),
    });
    const backend = createDanoBackendFromSession(session);
    const controller = await startDanoServer(
      {
        ...DEFAULT_BRIDGE_CONFIG,
        host: "127.0.0.1",
        port: 0,
        upload: {
          ...DEFAULT_BRIDGE_CONFIG.upload,
          uploadDir: path.join(root, "uploads"),
        },
      },
      { backend, captureSigint: false },
    );

    let sse: ReturnType<typeof waitForSseMessage> | undefined;
    try {
      const origin = controller.getBridgeUrl();
      if (!origin) throw new Error("Dano test server did not start");
      const createResponse = await fetch(`${origin}/api/clients`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const created = (await createResponse.json()) as {
        eventsUrl: string;
        messagesUrl: string;
      };
      sse = waitForSseMessage(
        `${origin}${created.eventsUrl}`,
        message =>
          message.type === "response" &&
          message.payload.id === "lazy-new-session",
        "lazy new_session response",
      );
      await sse.ready;
      const response = await fetch(`${origin}${created.messagesUrl}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "command",
          payload: { id: "lazy-new-session", type: "new_session" },
        } satisfies ClientMessage),
      });
      expect(response.status).toBe(202);
      const message = await sse.result;
      expect(message.payload).toMatchObject({
        command: "new_session",
        success: true,
        data: {
          model: { provider: "dano-settings", id: "configured" },
          thinkingLevel: "high",
        },
      });
      const sessionPath = (
        message.payload as { data?: { sessionPath?: string } }
      ).data?.sessionPath;
      expect(sessionPath).toEqual(expect.any(String));
      expect(fs.existsSync(sessionPath!)).toBe(false);
    } finally {
      sse?.close();
      await controller.stop();
      await backend.dispose();
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("projects Pi-owned session defaults, selections, and pending count", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "dano-pi-session-state-"));
    const provider = fauxProvider({
      provider: "dano-session-state",
      models: [
        { id: "fast", reasoning: true },
        { id: "default", reasoning: true },
      ],
      tokensPerSecond: 1,
    });
    provider.setResponses([
      fauxAssistantMessage(
        "This deliberately paced response keeps the real Pi session streaming.",
      ),
    ]);
    const modelRuntime = await ModelRuntime.create({
      authPath: path.join(root, "agent", "auth.json"),
      modelsPath: null,
    });
    modelRuntime.registerNativeProvider(provider.provider);
    await modelRuntime.setRuntimeApiKey("dano-session-state", "test-only");
    // Initial selection reads Pi's complete availability snapshot. A provider-only
    // query returns that provider's models without refreshing the global snapshot.
    await modelRuntime.getAvailable();
    const settingsManager = SettingsManager.inMemory({
      defaultProvider: "dano-session-state",
      defaultModel: "default",
      defaultThinkingLevel: "high",
    });
    const sessionManager = SessionManager.create(root, root);
    const { session } = await createAgentSession({
      cwd: root,
      agentDir: path.join(root, "agent"),
      modelRuntime,
      settingsManager,
      noTools: "all",
      sessionManager,
    });
    const backend = createDanoBackendFromSession(session);
    const controller = await startDanoServer(
      {
        ...DEFAULT_BRIDGE_CONFIG,
        host: "127.0.0.1",
        port: 0,
        upload: {
          ...DEFAULT_BRIDGE_CONFIG.upload,
          uploadDir: path.join(root, "uploads"),
        },
      },
      { backend, captureSigint: false },
    );

    const openSse: Array<ReturnType<typeof waitForSseMessage>> = [];
    try {
      const origin = controller.getBridgeUrl();
      if (!origin) throw new Error("Dano test server did not start");
      const createResponse = await fetch(`${origin}/api/clients`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const created = (await createResponse.json()) as {
        eventsUrl: string;
        messagesUrl: string;
      };
      const command = async (
        payload: Extract<ClientMessage, { type: "command" }>["payload"],
      ) => {
        const sse = waitForSseMessage(
          `${origin}${created.eventsUrl}`,
          message =>
            message.type === "response" && message.payload.id === payload.id,
          `response ${payload.id}`,
        );
        openSse.push(sse);
        await sse.ready;
        const response = await fetch(`${origin}${created.messagesUrl}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ type: "command", payload } satisfies ClientMessage),
        });
        expect(response.status).toBe(202);
        return sse.result;
      };

      const initial = await command({ id: "initial-state", type: "get_state" });
      expect(initial.payload).toMatchObject({
        data: {
          model: { provider: "dano-session-state", id: "default" },
          thinkingLevel: "high",
          pendingMessageCount: 0,
        },
      });
      const defaultModel = provider.getModel("default");
      expect(defaultModel).toBeDefined();
      expect(
        (initial.payload as { data?: { model?: unknown } }).data?.model,
      ).toEqual({
        id: defaultModel!.id,
        provider: defaultModel!.provider,
        name: defaultModel!.name,
        api: defaultModel!.api,
        reasoning: defaultModel!.reasoning,
        contextWindow: defaultModel!.contextWindow,
        maxTokens: defaultModel!.maxTokens,
      });

      await command({
        id: "select-model",
        type: "set_model",
        provider: "dano-session-state",
        modelId: "fast",
      });
      await command({
        id: "select-thinking",
        type: "set_thinking_level",
        level: "low",
      });
      const selected = await command({ id: "selected-state", type: "get_state" });
      expect(selected.payload).toMatchObject({
        data: {
          model: { provider: "dano-session-state", id: "fast" },
          thinkingLevel: "low",
        },
      });

      await command({ id: "start-stream", type: "steer", message: "start" });
      let streaming = false;
      for (let attempt = 0; attempt < 20 && !streaming; attempt += 1) {
        const state = await command({
          id: `streaming-state-${attempt}`,
          type: "get_state",
        });
        streaming = Boolean(
          (state.payload as { data?: { isStreaming?: boolean } }).data
            ?.isStreaming,
        );
        if (!streaming) await new Promise(resolve => setTimeout(resolve, 10));
      }
      expect(streaming).toBe(true);
      await command({ id: "queue-one", type: "follow_up", message: "one" });
      await command({ id: "queue-two", type: "follow_up", message: "two" });
      const queued = await command({ id: "queued-state", type: "get_state" });
      expect(queued.payload).toMatchObject({
        data: { pendingMessageCount: 2 },
      });
      await command({ id: "abort-stream", type: "abort" });
    } finally {
      for (const sse of openSse) sse.close();
      await controller.stop();
      await backend.dispose();
      fs.rmSync(root, { recursive: true, force: true });
    }
  }, 15_000);
});
