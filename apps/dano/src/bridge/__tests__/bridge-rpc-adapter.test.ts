import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { createAgentSessionMock } = vi.hoisted(() => ({
  createAgentSessionMock: vi.fn(),
}));

vi.mock("../detached-session.js", () => ({
  createDetachedAgentSession: createAgentSessionMock,
}));
import { BridgeEventBus } from "../bridge-event-bus.js";
import {
  DEFAULT_BRIDGE_CONFIG,
  type RpcCommand,
  type RpcExtensionUIResponse,
  type RpcWorkspaceEntry,
  type BridgeClient,
} from "../types.js";
import { BridgeRpcAdapter, type BridgeRpcAdapterContext } from "../bridge-rpc-adapter.js";
import {
  askUserQuestionCoordinator,
  askUserQuestionRuntime,
} from "../ask-user-question.js";

interface MockTransport {
  send: ReturnType<typeof vi.fn<(message: string) => void>>;
  trigger(event: string, ...args: unknown[]): void;
}

let adapter: BridgeRpcAdapter;
let client: BridgeClient;
let emitEvent: ReturnType<typeof vi.fn<(event: unknown) => void>>;
let uploadRegistry: {
  resolve: ReturnType<typeof vi.fn>;
  markReading: ReturnType<typeof vi.fn>;
  markReferenced: ReturnType<typeof vi.fn>;
  markDraft: ReturnType<typeof vi.fn>;
};

const createMockTransport = (): MockTransport => {
  return {
    send: vi.fn<(message: string) => void>(),
    trigger: (event: string, ...args: unknown[]) => {
      if (event === "message") {
        const data = args[0];
        adapter.handleRawMessage(Buffer.isBuffer(data) ? data.toString() : String(data));
        return;
      }
      if (event === "error") {
        const error = args[0] instanceof Error ? args[0] : new Error(String(args[0]));
        emitEvent({
          type: "command_error",
          client,
          commandType: "transport",
          error: error.message,
        });
        return;
      }
      if (event === "close") {
        adapter.dispose();
      }
    },
  };
};

function lastTranscriptUpsert(sendCalls: any[]) {
  for (let index = sendCalls.length - 1; index >= 0; index -= 1) {
    const call = sendCalls[index];
    if (call.payload?.type === "transcript_upsert") return call;
  }
  return undefined;
}

// Mock context
function runGit(cwd: string, args: string[]) {
  const result = spawnSync("git", args, {
    cwd,
    encoding: "utf8",
    windowsHide: true,
  });

  if (result.status !== 0) {
    throw new Error(
      result.stderr || result.stdout || `git ${args.join(" ")} failed`,
    );
  }
}

const REAL_GIT_TEST_TIMEOUT_MS = 15_000;

const createMockContext = (): BridgeRpcAdapterContext => {
  const sessionManager = {
    getCwd: vi.fn().mockReturnValue("/test/project"),
    getSessionDir: vi.fn().mockReturnValue("/path/to"),
    getSessionId: vi.fn().mockReturnValue("session-123"),
    getSessionFile: vi.fn().mockReturnValue("/path/to/session.json"),
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

  const model = {
    id: "gpt-4",
    name: "GPT-4",
    api: "openai-responses",
    provider: "openai",
    baseUrl: "https://example.com",
    reasoning: true,
    input: ["text"] as const,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128000,
    maxTokens: 8192,
  };

  const events = {
    subscribe: vi.fn().mockReturnValue(vi.fn()),
  };

  const state = {
    sessionManager: sessionManager as unknown as SessionManager,
    cwd: "/test/project",
    isIdle: vi.fn().mockReturnValue(true),
    hasPendingMessages: vi.fn().mockReturnValue(false),
    getAvailableModels: vi.fn().mockReturnValue([
      model,
      {
        ...model,
        id: "claude",
        name: "Claude",
        provider: "anthropic",
        api: "anthropic-messages",
        reasoning: false,
      },
    ]),
    getCurrentModel: vi.fn().mockReturnValue(model),
    getDefaultModel: vi.fn().mockReturnValue({
      provider: "openai",
      modelId: "gpt-4",
    }),
    getDefaultModels: vi.fn().mockReturnValue([
      {
        provider: "openai",
        modelId: "gpt-4",
      },
    ]),
    getDefaultThinkingLevel: vi.fn().mockReturnValue("medium"),
    getThinkingLevel: vi.fn().mockReturnValue("medium"),
    getContextUsage: vi
      .fn()
      .mockReturnValue({ tokens: 1000, contextWindow: 8000, percent: 12.5 }),
  };

  const actions = {
    sendUserMessage: vi.fn(),
    abort: vi.fn(),
    setModel: vi.fn().mockResolvedValue(undefined),
    setThinkingLevel: vi.fn(),
    setSessionName: vi.fn(),
    getCommands: vi
      .fn()
      .mockReturnValue([
        {
          name: "test",
          description: "Test command",
          source: "prompt" as const,
        },
      ]),
  };

  return { events, state, actions, askUserQuestion: askUserQuestionRuntime };
};

describe("BridgeRpcAdapter", () => {
  let ws: MockTransport;
  let context: BridgeRpcAdapterContext;
  let eventBus: BridgeEventBus;

  beforeEach(() => {
    createAgentSessionMock.mockReset();
    vi.spyOn(SessionManager, "listAll").mockResolvedValue([]);
    process.env.PI_WEB_SESSIONS_ROOT = fs.mkdtempSync(
      path.join(os.tmpdir(), "pi-web-sessions-root-"),
    );
    ws = createMockTransport();
    context = createMockContext();
    eventBus = new BridgeEventBus(DEFAULT_BRIDGE_CONFIG);
    emitEvent = vi.fn<(event: unknown) => void>();
    uploadRegistry = {
      resolve: vi.fn(),
      markReading: vi.fn(),
      markReferenced: vi.fn(),
      markDraft: vi.fn(),
    };
    client = {
      id: "test-client",
      seq: 1,
      connectedAt: new Date().toISOString(),
    };
    adapter = new BridgeRpcAdapter(
      client,
      message => ws.send(JSON.stringify(message)),
      context,
      {
        ...DEFAULT_BRIDGE_CONFIG,
        slashCommandsAndMentionsEnabled: true,
      },
      eventBus,
      emitEvent as any,
      uploadRegistry as any,
    );
  });

  it("exposes the current workspace cwd for bridge-owned upload routing", () => {
    expect(adapter.currentGitCwd()).toBe("/test/project");
  });

  describe("command dispatch", () => {
    it("should handle prompt command by auto-creating a session", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-prompt-"));
      const sessionFile = path.join(tmpDir, "session.jsonl");
      // Write a minimal session header so SessionManager.open works
      fs.writeFileSync(
        sessionFile,
        JSON.stringify({
          type: "session",
          version: 3,
          id: "live-session",
          timestamp: new Date().toISOString(),
          cwd: tmpDir,
        }),
      );
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);

      const promptSpy = vi.fn().mockResolvedValue(undefined);
      createAgentSessionMock.mockResolvedValue({
        session: {
          sessionFile: undefined, // will be set by autoCreateSession
          sessionId: "auto-session",
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: vi.fn().mockReturnValue(() => {}),
          prompt: promptSpy,
          sessionManager: {
            getSessionFile: vi.fn().mockReturnValue(undefined),
            getSessionId: vi.fn().mockReturnValue("auto-session"),
            getEntries: vi.fn().mockReturnValue([]),
            getBranch: vi.fn().mockReturnValue([]),
            getCwd: vi.fn().mockReturnValue(tmpDir),
          },
        },
      });

      const command: RpcCommand = {
        id: "cmd-1",
        type: "prompt",
        message: "Hello",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      // Wait for async handling
      await new Promise(r => setTimeout(r, 30));

      // Should NOT call pi.sendUserMessage (that would trigger TUI switch)
      expect(context.actions.sendUserMessage).not.toHaveBeenCalled();
      expect(emitEvent).toHaveBeenCalledWith({
        type: "command_received",
        client,
        commandType: "prompt",
        correlationId: "cmd-1",
      });

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("keeps disabled slash commands literal for idle and streaming prompts", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-prompt-"));
      const sessionFile = path.join(tmpDir, "session.jsonl");
      fs.writeFileSync(
        sessionFile,
        JSON.stringify({
          type: "session",
          version: 3,
          id: "live-session",
          timestamp: new Date().toISOString(),
          cwd: tmpDir,
        }),
      );
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);

      const promptSpy = vi.fn().mockResolvedValue(undefined);
      const session = {
        sessionFile: undefined as string | undefined,
        sessionId: "auto-session",
        isStreaming: false,
        bindExtensions: vi.fn().mockResolvedValue(undefined),
        subscribe: vi.fn().mockReturnValue(() => {}),
        prompt: promptSpy,
        sessionManager: {
          getSessionFile: vi.fn().mockReturnValue(undefined),
          getSessionId: vi.fn().mockReturnValue("auto-session"),
          getEntries: vi.fn().mockReturnValue([]),
          getBranch: vi.fn().mockReturnValue([]),
          getCwd: vi.fn().mockReturnValue(tmpDir),
        },
      };
      createAgentSessionMock.mockResolvedValue({ session });

      adapter.dispose();
      adapter = new BridgeRpcAdapter(
        client,
        message => ws.send(JSON.stringify(message)),
        context,
        DEFAULT_BRIDGE_CONFIG,
        eventBus,
        emitEvent as any,
        uploadRegistry as any,
      );

      ws.trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "disabled-idle",
              type: "prompt",
              message: "/compact keep this literal",
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 30));

      expect(promptSpy).toHaveBeenNthCalledWith(
        1,
        "/compact keep this literal",
        expect.objectContaining({
          source: "rpc",
          expandPromptTemplates: false,
        }),
      );

      session.isStreaming = true;
      ws.trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "disabled-streaming",
              type: "prompt",
              message: "/skill:review",
              streamingBehavior: "followUp",
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 30));

      expect(promptSpy).toHaveBeenNthCalledWith(
        2,
        "/skill:review",
        expect.objectContaining({
          source: "rpc",
          streamingBehavior: "followUp",
          expandPromptTemplates: false,
        }),
      );

      ws.trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "disabled-steering",
              type: "prompt",
              message: "/template-review",
              streamingBehavior: "steer",
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 30));

      expect(promptSpy).toHaveBeenNthCalledWith(
        3,
        "/template-review",
        expect.objectContaining({
          source: "rpc",
          streamingBehavior: "steer",
          expandPromptTemplates: false,
        }),
      );

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("passes enabled extension, prompt, and skill commands to Pi expansion", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-commands-"));
      const sessionFile = path.join(tmpDir, "session.jsonl");
      fs.writeFileSync(
        sessionFile,
        JSON.stringify({
          type: "session",
          version: 3,
          id: "command-session",
          timestamp: new Date().toISOString(),
          cwd: tmpDir,
        }),
      );
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);

      const prompt = vi.fn().mockResolvedValue(undefined);
      createAgentSessionMock.mockResolvedValue({
        session: {
          sessionFile: undefined,
          sessionId: "command-session",
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: vi.fn().mockReturnValue(() => {}),
          prompt,
          sessionManager: {
            getSessionFile: vi.fn().mockReturnValue(undefined),
            getSessionId: vi.fn().mockReturnValue("command-session"),
            getEntries: vi.fn().mockReturnValue([]),
            getBranch: vi.fn().mockReturnValue([]),
            getCwd: vi.fn().mockReturnValue(tmpDir),
          },
        },
      });

      for (const [index, message] of [
        "/deploy:2 production",
        "/review current changes",
        "/skill:audit repository",
      ].entries()) {
        ws.trigger(
          "message",
          Buffer.from(
            JSON.stringify({
              type: "command",
              payload: {
                id: `enabled-command-${index}`,
                type: "prompt",
                message,
              },
            }),
          ),
        );
        await vi.waitFor(() => {
          expect(prompt).toHaveBeenCalledTimes(index + 1);
        });
      }

      for (const [index, message] of [
        "/deploy:2 production",
        "/review current changes",
        "/skill:audit repository",
      ].entries()) {
        const [actualMessage, options] = prompt.mock.calls[index] as [
          string,
          Record<string, unknown>,
        ];
        expect(actualMessage).toBe(message);
        expect(options).toMatchObject({ source: "rpc" });
        expect(options).not.toHaveProperty("expandPromptTemplates");
      }

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("keeps explicit prompt images and passes files as hidden project refs", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-attach-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: [{ type: "text", text: "Initial" }],
        timestamp: Date.now(),
      });
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) throw new Error("session file was not created");

      const rawEntries = sessionManager.getEntries();
      const header = {
        type: "session",
        version: 3,
        id: sessionManager.getSessionId(),
        timestamp: new Date().toISOString(),
        cwd: tmpDir,
      };
      fs.writeFileSync(
        sessionFile,
        [
          JSON.stringify(header),
          ...rawEntries.map(e => JSON.stringify(e)),
        ].join("\n"),
      );
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);
      (
        context.state.sessionManager.getCwd as ReturnType<typeof vi.fn>
      ).mockReturnValue(tmpDir);

      const promptSpy = vi.fn().mockResolvedValue(undefined);
      const sendCustomMessageSpy = vi.fn().mockResolvedValue(undefined);
      const subscribeSpy = vi.fn().mockReturnValue(() => {});
      createAgentSessionMock.mockResolvedValue({
        session: {
          sessionFile,
          sessionId: sessionManager.getSessionId(),
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: subscribeSpy,
          prompt: promptSpy,
          sendCustomMessage: sendCustomMessageSpy,
          sessionManager,
        },
      });

      // Now send prompt with image
      fs.mkdirSync(path.join(tmpDir, "uploads"), { recursive: true });
      fs.writeFileSync(path.join(tmpDir, "uploads", "sample.pdf"), "fake upload");
      uploadRegistry.resolve.mockReturnValue({
        id: "upload-1",
        name: "sample.pdf",
        size: 14,
        mimeType: "application/pdf",
        path: path.join(tmpDir, "uploads", "sample.pdf"),
        relativePath: "uploads/sample.pdf",
      });

      const command: RpcCommand = {
        id: "cmd-2",
        type: "prompt",
        message: "Inspect this image",
        images: [
          {
            type: "image",
            mimeType: "image/png",
            data: "ZmFrZS1pbWFnZQ==",
          },
        ],
        files: [
          {
            id: "upload-1",
            name: "sample.pdf",
            size: 14,
            mimeType: "application/pdf",
            path: path.join(tmpDir, "uploads", "sample.pdf"),
            relativePath: "uploads/sample.pdf",
          } as any,
        ],
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 50));

      expect(uploadRegistry.markReferenced).toHaveBeenCalledWith("upload-1", {
        correlationId: "cmd-2",
      });
      expect(sendCustomMessageSpy).not.toHaveBeenCalled();
      expect(context.actions.sendUserMessage).not.toHaveBeenCalled();
      expect(promptSpy).toHaveBeenCalledWith(
        `Inspect this image\n\nProject file references:\n- ${path.join(tmpDir, "uploads", "sample.pdf")} (sample.pdf)`,
        {
          source: "rpc",
          images: [
            {
              type: "image",
              mimeType: "image/png",
              data: "ZmFrZS1pbWFnZQ==",
            },
          ],
        },
      );
      (ws.send as ReturnType<typeof vi.fn>).mockClear();
      const sessionEventHandler = subscribeSpy.mock.calls[0]?.[0] as
        | ((event: object) => void)
        | undefined;
      sessionEventHandler?.({
        type: "message_start",
        message: {
          role: "user",
          content: [
            {
              type: "text",
              text: `Inspect this image\n\nProject file references:\n- ${path.join(tmpDir, "uploads", "sample.pdf")} (sample.pdf)`,
            },
            {
              type: "image",
              mimeType: "image/png",
              data: "ZmFrZS1pbWFnZQ==",
            },
          ],
        },
      });

      const transcriptStart = (ws.send as ReturnType<typeof vi.fn>).mock.calls
        .map(call => JSON.parse(call[0] as string))
        .find(
          call =>
            call.type === "event" && call.payload.type === "transcript_start",
        );
      expect(transcriptStart?.payload.message.content).toEqual([
        { type: "text", text: "Inspect this image" },
        {
          type: "image",
          mimeType: "image/png",
          data: "ZmFrZS1pbWFnZQ==",
        },
        {
          type: "file",
          id: "upload-1",
          name: "sample.pdf",
          size: 14,
          mimeType: "application/pdf",
          path: "uploads/sample.pdf",
          relativePath: "uploads/sample.pdf",
          previewUrl: undefined,
        },
      ]);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("projects plain prompt text when the live user start event is empty", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-prompt-text-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) throw new Error("session file was not created");
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);

      const subscribeSpy = vi.fn().mockReturnValue(() => {});
      createAgentSessionMock.mockResolvedValue({
        session: {
          sessionFile,
          sessionId: sessionManager.getSessionId(),
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: subscribeSpy,
          prompt: vi.fn().mockResolvedValue(undefined),
          sessionManager,
        },
      });

      const text = `ask_user_question {
  "question": "请填写说明",
  "inputType": "textarea",
  "default": "默认内容"
}`;
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: { id: "cmd-plain-text", type: "prompt", message: text },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 30));

      (ws.send as ReturnType<typeof vi.fn>).mockClear();
      const sessionEventHandler = subscribeSpy.mock.calls[0]?.[0] as
        | ((event: object) => void)
        | undefined;
      sessionEventHandler?.({
        type: "message_start",
        message: {
          role: "user",
          content: [],
        },
      });

      const transcriptStart = (ws.send as ReturnType<typeof vi.fn>).mock.calls
        .map(call => JSON.parse(call[0] as string))
        .find(
          call =>
            call.type === "event" && call.payload.type === "transcript_start",
        );
      expect(transcriptStart?.payload.message.content).toEqual([
        { type: "text", text },
      ]);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("passes uploaded files as project file references in live steering", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-upload-"));
      const sourceDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-upload-source-"));
      try {
        (context.state as { cwd: string }).cwd = tmpDir;
        const filePath = path.join(sourceDir, "uploads", "sample.pdf");
        fs.mkdirSync(path.dirname(filePath), { recursive: true });
        fs.writeFileSync(filePath, "fake upload");
        uploadRegistry.resolve.mockReturnValue({
          id: "upload-1",
          name: "sample.pdf",
          size: 14,
          mimeType: "application/pdf",
          path: filePath,
          relativePath: "uploads/sample.pdf",
        });

        const command: RpcCommand = {
          id: "cmd-upload",
          type: "steer",
          message: "Inspect upload",
          files: [
            {
              id: "upload-1",
              name: "sample.pdf",
              size: 14,
              mimeType: "application/pdf",
              path: filePath,
              relativePath: "uploads/sample.pdf",
            } as any,
          ],
        };
        (
          ws as unknown as { trigger: (event: string, data: Buffer) => void }
        ).trigger(
          "message",
          Buffer.from(JSON.stringify({ type: "command", payload: command })),
        );

        await new Promise(r => setTimeout(r, 20));

        expect(uploadRegistry.markReading).not.toHaveBeenCalled();
        expect(uploadRegistry.markReferenced).toHaveBeenCalledWith("upload-1", {
          correlationId: "cmd-upload",
        });
        expect(fs.readFileSync(path.join(tmpDir, "uploads", "sample.pdf"), "utf8")).toBe(
          "fake upload",
        );
        expect(context.actions.sendUserMessage).toHaveBeenCalledWith(
          `Inspect upload\n\nProject file references:\n- ${path.join(tmpDir, "uploads", "sample.pdf")} (sample.pdf)`,
          { deliverAs: "steer" },
        );
      } finally {
        fs.rmSync(tmpDir, { recursive: true, force: true });
        fs.rmSync(sourceDir, { recursive: true, force: true });
      }
    });

    it("passes uploaded image files to the model without requiring JSON image payloads", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-upload-"));
      const sourceDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-upload-source-"));
      try {
        (context.state as { cwd: string }).cwd = tmpDir;
        const filePath = path.join(sourceDir, "uploads", "sample.png");
        const imageBytes = Buffer.from("fake-image");
        fs.mkdirSync(path.dirname(filePath), { recursive: true });
        fs.writeFileSync(filePath, imageBytes);
        uploadRegistry.resolve.mockReturnValue({
          id: "upload-1",
          name: "sample.png",
          size: imageBytes.length,
          mimeType: "image/png",
          path: filePath,
          relativePath: "uploads/sample.png",
          previewUrl: "/api/uploads/upload-1/preview",
        });

        const command: RpcCommand = {
          id: "cmd-upload-image",
          type: "steer",
          message: "Inspect upload",
          files: [
            {
              id: "upload-1",
              name: "sample.png",
              size: imageBytes.length,
              mimeType: "image/png",
              path: filePath,
              relativePath: "uploads/sample.png",
              previewUrl: "/api/uploads/upload-1/preview",
            } as any,
          ],
        };
        (
          ws as unknown as { trigger: (event: string, data: Buffer) => void }
        ).trigger(
          "message",
          Buffer.from(JSON.stringify({ type: "command", payload: command })),
        );

        await new Promise(r => setTimeout(r, 20));

        expect(context.actions.sendUserMessage).toHaveBeenCalledWith(
          [
            {
              type: "text",
              text: `Inspect upload\n\nProject file references:\n- ${path.join(tmpDir, "uploads", "sample.png")} (sample.png)`,
            },
            {
              type: "image",
              data: imageBytes.toString("base64"),
              mimeType: "image/png",
            },
          ],
          { deliverAs: "steer" },
        );
      } finally {
        fs.rmSync(tmpDir, { recursive: true, force: true });
        fs.rmSync(sourceDir, { recursive: true, force: true });
      }
    });

    it("rejects uploaded files without project paths", async () => {
      const filePath = path.join(os.tmpdir(), "missing-upload.png");
      uploadRegistry.resolve.mockReturnValue({
        id: "upload-1",
        name: "sample.png",
        size: 14,
        mimeType: "image/png",
        path: filePath,
      });

      const command: RpcCommand = {
        id: "cmd-upload-fail",
        type: "steer",
        message: "Inspect upload",
        files: [
          {
            id: "upload-1",
            name: "sample.png",
            size: 14,
            mimeType: "image/png",
            path: filePath,
          },
        ],
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 20));

      expect(uploadRegistry.markReading).not.toHaveBeenCalled();
      expect(uploadRegistry.markDraft).not.toHaveBeenCalled();
      expect(uploadRegistry.markReferenced).not.toHaveBeenCalled();
      expect(context.actions.sendUserMessage).not.toHaveBeenCalled();
      expect(emitEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "command_error",
          commandType: "steer",
          correlationId: "cmd-upload-fail",
        }),
      );
    });

    it("continues the selected session instead of using pi.sendUserMessage", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-session-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: [{ type: "text", text: "Selected session" }],
        timestamp: Date.now(),
      });
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }

      // SessionManager.create doesn't immediately flush to disk. Force persist
      // by re-opening from the in-memory entries.
      const rawEntries = sessionManager.getEntries();
      const header = {
        type: "session",
        version: 3,
        id: sessionManager.getSessionId(),
        timestamp: new Date().toISOString(),
        cwd: tmpDir,
      };
      const lines = [JSON.stringify(header)];
      for (const entry of rawEntries) {
        lines.push(JSON.stringify(entry));
      }
      fs.writeFileSync(sessionFile, lines.join("\n"));
      const resumedSessionManager = SessionManager.open(sessionFile);

      const promptSpy = vi.fn().mockResolvedValue(undefined);
      const subscribeSpy = vi.fn().mockReturnValue(() => {});
      createAgentSessionMock.mockResolvedValue({
        session: {
          sessionFile,
          sessionId: resumedSessionManager.getSessionId(),
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: subscribeSpy,
          prompt: promptSpy,
          sessionManager: resumedSessionManager,
        },
      });

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "switch-1",
              type: "switch_session",
              sessionPath: sessionFile,
            },
          }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "prompt-1",
              type: "prompt",
              message: "Continue here",
            },
          }),
        ),
      );

      await new Promise(r => setTimeout(r, 20));

      expect(context.actions.sendUserMessage).not.toHaveBeenCalled();
      expect(createAgentSessionMock).toHaveBeenCalledTimes(1);
      expect(promptSpy).toHaveBeenCalledWith("Continue here", {
        source: "rpc",
      });

      resumedSessionManager.appendMessage({
        role: "user",
        content: [{ type: "text", text: "Continue here" }],
        timestamp: Date.now(),
      } as any);
      const newUserEntry = resumedSessionManager.getLeafEntry();
      resumedSessionManager.appendMessage({
        role: "assistant",
        content: [{ type: "text", text: "Done" }],
        timestamp: Date.now(),
        provider: "test",
        model: "test",
        api: "test",
        usage: {
          input: 0,
          output: 0,
          cacheRead: 0,
          cacheWrite: 0,
          totalTokens: 0,
          cost: {
            input: 0,
            output: 0,
            cacheRead: 0,
            cacheWrite: 0,
            total: 0,
          },
        },
        stopReason: "stop",
      } as any);
      const selectedSessionEventHandler = subscribeSpy.mock.calls[0]?.[0] as
        | ((event: object) => void)
        | undefined;
      selectedSessionEventHandler?.({
        type: "agent_end",
      });

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const latestSnapshot = [...sendCalls]
        .reverse()
        .find(
          call =>
            call.type === "event" &&
            call.payload.type === "transcript_snapshot" &&
            call.payload.sessionPath === sessionFile,
        );
      expect(
        latestSnapshot?.payload.messages.findLast(
          (message: any) => message.role === "user",
        ),
      ).toMatchObject({
        id: newUserEntry?.id,
        role: "user",
        content: [{ type: "text", text: "Continue here" }],
      });
    });

    it("passes prompt attachments through when continuing the selected session", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-session-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: [{ type: "text", text: "Selected session" }],
        timestamp: Date.now(),
      });
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }

      const rawEntries = sessionManager.getEntries();
      const header = {
        type: "session",
        version: 3,
        id: sessionManager.getSessionId(),
        timestamp: new Date().toISOString(),
        cwd: tmpDir,
      };
      const lines = [JSON.stringify(header)];
      for (const entry of rawEntries) {
        lines.push(JSON.stringify(entry));
      }
      fs.writeFileSync(sessionFile, lines.join("\n"));

      const promptSpy = vi.fn().mockResolvedValue(undefined);
      const subscribeSpy = vi.fn().mockReturnValue(() => {});
      createAgentSessionMock.mockResolvedValue({
        session: {
          sessionFile,
          sessionId: sessionManager.getSessionId(),
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: subscribeSpy,
          prompt: promptSpy,
          sessionManager,
        },
      });

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "switch-attachments",
              type: "switch_session",
              sessionPath: sessionFile,
            },
          }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "prompt-attachments",
              type: "prompt",
              message: "Continue with context",
              images: [
                {
                  type: "image",
                  mimeType: "image/webp",
                  data: "d2VicA==",
                },
              ],
            },
          }),
        ),
      );

      await new Promise(r => setTimeout(r, 20));

      expect(promptSpy).toHaveBeenCalledWith("Continue with context", {
        source: "rpc",
        images: [
          {
            type: "image",
            mimeType: "image/webp",
            data: "d2VicA==",
          },
        ],
      });
    });

    it("should handle steer command", async () => {
      const command: RpcCommand = {
        id: "cmd-1",
        type: "steer",
        message: "Steer message",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      expect(context.actions.sendUserMessage).toHaveBeenCalledWith(
        "Steer message",
        {
          deliverAs: "steer",
        },
      );
    });

    it("should handle follow_up command", async () => {
      const command: RpcCommand = {
        id: "cmd-1",
        type: "follow_up",
        message: "Follow up",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      expect(context.actions.sendUserMessage).toHaveBeenCalledWith(
        "Follow up",
        {
          deliverAs: "followUp",
        },
      );
    });

    it("should handle abort command", async () => {
      const command: RpcCommand = { id: "cmd-1", type: "abort" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      expect(context.actions.abort).toHaveBeenCalled();
    });

    it("resolves a pending question through the command channel", async () => {
      const pending = askUserQuestionCoordinator.wait(
        "question-call-1",
        {
          question: "Keep or replace?",
          options: ["Keep", "Replace"],
          default: "Keep",
        },
        undefined,
      );
      const command: RpcCommand = {
        id: "answer-1",
        type: "answer_question",
        toolCallId: "question-call-1",
        cancelled: false,
        answer: "Keep",
      };

      ws.trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await expect(pending).resolves.toEqual({
        status: "answered",
        answer: "Keep",
      });
      await new Promise(r => setTimeout(r, 10));
      const response = JSON.parse(ws.send.mock.calls.at(-1)?.[0] ?? "null");
      expect(response.payload).toMatchObject({
        id: "answer-1",
        command: "answer_question",
        success: true,
        data: { status: "answered", answer: "Keep" },
      });
    });

    it("acknowledges presentation for the matching question tool call", async () => {
      const pending = askUserQuestionCoordinator.wait(
        "question-call-presented",
        { question: "Keep or replace?", options: ["Keep", "Replace"], default: "Keep" },
        undefined,
      );
      const command: RpcCommand = {
        id: "present-1",
        type: "present_question",
        toolCallId: "question-call-presented",
      };

      ws.trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );
      await new Promise(r => setTimeout(r, 10));

      expect(
        (ws.send as ReturnType<typeof vi.fn>).mock.calls
          .map(call => JSON.parse(call[0] as string))
          .some(message =>
            message.type === "response" &&
            message.payload.command === "present_question" &&
            message.payload.success === true,
          ),
      ).toBe(true);

      askUserQuestionCoordinator.answer("question-call-presented", {
        cancelled: true,
      });
      await expect(pending).resolves.toEqual({ status: "cancelled" });
    });

    it("runs field assist through the command channel without transcript events", async () => {
      context.fieldAssist = {
        assist: vi.fn().mockResolvedValue({
          value: "润色后",
          metadata: {
            action: "polish",
            fieldType: "input",
            inputLength: 2,
            outputLength: 3,
            elapsedMs: 1,
          },
        }),
      };
      const command: RpcCommand = {
        id: "field-1",
        type: "field_assist",
        requestId: "question-1",
        action: "polish",
        fieldType: "input",
        requestMethod: "input",
        title: "事由",
        currentValue: "请假",
      };

      ws.trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );
      await new Promise(r => setTimeout(r, 10));

      expect(context.fieldAssist.assist).toHaveBeenCalledWith(command, {
        clientId: "test-client",
      });
      expect(context.actions.sendUserMessage).not.toHaveBeenCalled();
      const response = JSON.parse(ws.send.mock.calls.at(-1)?.[0] ?? "null");
      expect(response.payload).toMatchObject({
        id: "field-1",
        command: "field_assist",
        success: true,
        data: { value: "润色后" },
      });
    });

    it("should handle get_state command", async () => {
      const command: RpcCommand = { id: "cmd-1", type: "get_state" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      expect(sendCalls.length).toBeGreaterThan(0);

      const response = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "get_state" &&
          call.payload.success,
      );

      expect(response?.type).toBe("response");
      expect(response?.payload.command).toBe("get_state");
      expect(response?.payload.success).toBe(true);
      expect(response?.payload.data).toHaveProperty("sessionId", "session-123");
      expect(response?.payload.data).toHaveProperty("messageCount", 1);
      expect(response?.payload.data).toHaveProperty("sessionName", "Hello");

      const statsEvent = sendCalls.find(
        call => call.type === "event" && call.payload.type === "session_stats",
      );
      expect(statsEvent?.payload).toMatchObject({
        type: "session_stats",
        stats: {
          tokens: 1000,
          contextWindow: 8000,
          percent: 12.5,
        },
      });
    });

    it("returns the live branch model when get_state has no live current model", async () => {
      (context.state.getCurrentModel as ReturnType<typeof vi.fn>).mockReturnValue(
        undefined,
      );
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockReturnValue([
        {
          type: "model_change",
          provider: "openai",
          modelId: "gpt-4.1",
        },
      ]);

      const command: RpcCommand = { id: "cmd-model", type: "get_state" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const response = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "get_state" &&
          call.payload.id === "cmd-model",
      );

      expect(response?.payload.success).toBe(true);
      expect(response?.payload.data.model).toEqual({
        provider: "openai",
        id: "gpt-4.1",
      });
    });

    it("initializes live get_state from the default available model", async () => {
      (context.state.getCurrentModel as ReturnType<typeof vi.fn>).mockReturnValue(
        undefined,
      );
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockReturnValue([]);
      (
        context.state.sessionManager.getEntries as ReturnType<typeof vi.fn>
      ).mockReturnValue([]);

      const command: RpcCommand = { id: "cmd-default-model", type: "get_state" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const response = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "get_state" &&
          call.payload.id === "cmd-default-model",
      );

      expect(response?.payload.success).toBe(true);
      expect(response?.payload.data.model).toMatchObject({
        provider: "openai",
        id: "gpt-4",
      });
      expect(response?.payload.data.thinkingLevel).toBe("medium");
      expect(
        context.state.sessionManager.appendModelChange,
      ).toHaveBeenCalledWith("openai", "gpt-4");
      expect(
        context.state.sessionManager.appendThinkingLevelChange,
      ).toHaveBeenCalledWith("medium");
    });

    it("keeps get_state model empty when no models are available", async () => {
      (context.state.getCurrentModel as ReturnType<typeof vi.fn>).mockReturnValue(
        undefined,
      );
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockReturnValue([]);
      (context.state.getAvailableModels as ReturnType<typeof vi.fn>).mockReturnValue(
        [],
      );

      const command: RpcCommand = { id: "cmd-no-model", type: "get_state" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const response = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "get_state" &&
          call.payload.id === "cmd-no-model",
      );

      expect(response?.payload.success).toBe(true);
      expect(response?.payload.data.model).toBeUndefined();
      expect(response?.payload.data.thinkingLevel).toBe("off");
      expect(
        context.state.sessionManager.appendModelChange,
      ).not.toHaveBeenCalled();
      expect(
        context.state.sessionManager.appendThinkingLevelChange,
      ).toHaveBeenCalledWith("off");
    });

    it("returns the selected model after set_model", async () => {
      (context.actions.setModel as ReturnType<typeof vi.fn>).mockImplementation(
        async model => {
          (
            context.state.getCurrentModel as ReturnType<typeof vi.fn>
          ).mockReturnValue(model);
        },
      );

      const setModelCommand: RpcCommand = {
        id: "cmd-set-model",
        type: "set_model",
        provider: "anthropic",
        modelId: "claude",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: setModelCommand }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      const stateCommand: RpcCommand = {
        id: "cmd-state-after-set",
        type: "get_state",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: stateCommand })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const response = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "get_state" &&
          call.payload.id === "cmd-state-after-set",
      );

      expect(context.actions.setModel).toHaveBeenCalledWith(
        expect.objectContaining({ provider: "anthropic", id: "claude" }),
      );
      expect(response?.payload.success).toBe(true);
      expect(response?.payload.data.model).toMatchObject({
        provider: "anthropic",
        id: "claude",
      });
    });

    it("should list git branches for the active repo", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-git-list-"));
      fs.writeFileSync(path.join(tmpDir, "README.md"), "hello\n");
      runGit(tmpDir, ["init"]);
      runGit(tmpDir, ["config", "user.name", "Pi Web"]);
      runGit(tmpDir, ["config", "user.email", "pi-web@example.com"]);
      runGit(tmpDir, ["add", "README.md"]);
      runGit(tmpDir, ["commit", "-m", "init"]);
      runGit(tmpDir, ["branch", "-M", "main"]);
      runGit(tmpDir, ["branch", "feature"]);

      (
        context.state.sessionManager.getCwd as ReturnType<typeof vi.fn>
      ).mockReturnValue(tmpDir);
      context.state.cwd = tmpDir;

      const command: RpcCommand = {
        id: "cmd-git-list",
        type: "list_git_branches",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const response = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "list_git_branches" &&
          call.payload.success,
      );

      expect(response?.payload.data.headLabel).toBe("main");
      expect(response?.payload.data.currentBranch).toBe("main");
      expect(response?.payload.data.detached).toBe(false);
      expect(response?.payload.data.branches).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            name: "main",
            shortName: "main",
            kind: "local",
            isCurrent: true,
          }),
          expect.objectContaining({
            name: "feature",
            shortName: "feature",
            kind: "local",
            isCurrent: false,
          }),
        ]),
      );

      fs.rmSync(tmpDir, { recursive: true, force: true });
    }, REAL_GIT_TEST_TIMEOUT_MS);

    it("should switch git branches for the active repo", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-git-switch-"),
      );
      fs.writeFileSync(path.join(tmpDir, "README.md"), "hello\n");
      runGit(tmpDir, ["init"]);
      runGit(tmpDir, ["config", "user.name", "Pi Web"]);
      runGit(tmpDir, ["config", "user.email", "pi-web@example.com"]);
      runGit(tmpDir, ["add", "README.md"]);
      runGit(tmpDir, ["commit", "-m", "init"]);
      runGit(tmpDir, ["branch", "-M", "main"]);
      runGit(tmpDir, ["branch", "feature"]);

      (
        context.state.sessionManager.getCwd as ReturnType<typeof vi.fn>
      ).mockReturnValue(tmpDir);
      context.state.cwd = tmpDir;

      const command: RpcCommand = {
        id: "cmd-git-switch",
        type: "switch_git_branch",
        branchName: "feature",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const response = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "switch_git_branch" &&
          call.payload.success,
      );

      expect(response?.payload.data.headLabel).toBe("feature");
      expect(response?.payload.data.currentBranch).toBe("feature");
      expect(response?.payload.data.branches).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ name: "main", isCurrent: false }),
          expect.objectContaining({ name: "feature", isCurrent: true }),
        ]),
      );

      const currentBranch = spawnSync("git", ["branch", "--show-current"], {
        cwd: tmpDir,
        encoding: "utf8",
        windowsHide: true,
      }).stdout.trim();
      expect(currentBranch).toBe("feature");

      fs.rmSync(tmpDir, { recursive: true, force: true });
    }, REAL_GIT_TEST_TIMEOUT_MS);

    it("should create and switch to a new git branch", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-git-create-"),
      );
      fs.writeFileSync(path.join(tmpDir, "README.md"), "hello\n");
      runGit(tmpDir, ["init"]);
      runGit(tmpDir, ["config", "user.name", "Pi Web"]);
      runGit(tmpDir, ["config", "user.email", "pi-web@example.com"]);
      runGit(tmpDir, ["add", "README.md"]);
      runGit(tmpDir, ["commit", "-m", "init"]);
      runGit(tmpDir, ["branch", "-M", "main"]);

      (
        context.state.sessionManager.getCwd as ReturnType<typeof vi.fn>
      ).mockReturnValue(tmpDir);
      context.state.cwd = tmpDir;

      const command: RpcCommand = {
        id: "cmd-git-create",
        type: "create_git_branch",
        branchName: "feature/new-ui",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const response = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "create_git_branch" &&
          call.payload.success,
      );

      expect(response?.payload.data.headLabel).toBe("feature/new-ui");
      expect(response?.payload.data.currentBranch).toBe("feature/new-ui");
      expect(response?.payload.data.branches).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ name: "main", isCurrent: false }),
          expect.objectContaining({
            name: "feature/new-ui",
            shortName: "feature/new-ui",
            kind: "local",
            isCurrent: true,
          }),
        ]),
      );

      const currentBranch = spawnSync("git", ["branch", "--show-current"], {
        cwd: tmpDir,
        encoding: "utf8",
        windowsHide: true,
      }).stdout.trim();
      expect(currentBranch).toBe("feature/new-ui");

      fs.rmSync(tmpDir, { recursive: true, force: true });
    }, REAL_GIT_TEST_TIMEOUT_MS);

    it("should handle get_messages command", async () => {
      const command: RpcCommand = { id: "cmd-1", type: "get_messages" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      expect(sendCalls.length).toBeGreaterThan(0);

      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.type).toBe("response");
      expect(response.payload.command).toBe("get_messages");
      expect(response.payload.success).toBe(true);
      expect(response.payload.data.messages).toHaveLength(1);
      expect(response.payload.data.direction).toBe("latest");
      expect(response.payload.data.hasOlder).toBe(false);
    });

    it("projects recovered question lifecycle states without resurrecting orphan cards", async () => {
      const messages = [
        {
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "answered-question",
              name: "ask_user_question",
              arguments: { question: "审批人？", default: "张三" },
            },
            {
              type: "toolCall",
              id: "orphan-question",
              name: "ask_user_question",
              arguments: { question: "请假原因？", default: "个人事务" },
            },
          ],
        },
        {
          role: "toolResult",
          toolCallId: "answered-question",
          toolName: "ask_user_question",
          content: [{ type: "text", text: "" }],
          details: { status: "answered", answer: "李四" },
          isError: false,
        },
      ];
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockReturnValue(messages);
      (
        context.state.sessionManager.getEntries as ReturnType<typeof vi.fn>
      ).mockReturnValue(messages);

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: { id: "cmd-question-recovery", type: "get_messages" },
          }),
        ),
      );
      await new Promise(resolve => setTimeout(resolve, 10));

      const response = (ws.send as ReturnType<typeof vi.fn>).mock.calls
        .map(([message]) => JSON.parse(message as string))
        .find(message => message.payload?.id === "cmd-question-recovery");
      const content = response.payload.data.messages[0].content;
      expect(content[0]).toMatchObject({
        id: "answered-question",
        questionState: "answered",
      });
      expect(content[1]).toMatchObject({
        id: "orphan-question",
        questionState: "terminal_failure",
      });
    });

    it("does not project question lifecycle states onto other tools", async () => {
      const messages = [
        {
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "successful-bash",
              name: "bash",
              arguments: { command: "printf success" },
            },
            {
              type: "toolCall",
              id: "failed-bash",
              name: "bash",
              arguments: { command: "exit 7" },
            },
          ],
        },
        {
          role: "toolResult",
          toolCallId: "successful-bash",
          toolName: "bash",
          content: [{ type: "text", text: "success" }],
          isError: false,
        },
        {
          role: "toolResult",
          toolCallId: "failed-bash",
          toolName: "bash",
          content: [{ type: "text", text: "failed" }],
          isError: true,
        },
      ];
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockReturnValue(messages);

      ws.trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: { id: "cmd-non-question-recovery", type: "get_messages" },
          }),
        ),
      );
      await new Promise(resolve => setTimeout(resolve, 10));

      const response = (ws.send as ReturnType<typeof vi.fn>).mock.calls
        .map(([message]) => JSON.parse(message as string))
        .find(message => message.payload?.id === "cmd-non-question-recovery");
      expect(response.payload.data.messages[0].content).toEqual(
        messages[0].content,
      );
    });

    it("correlates recovered question results across transcript page boundaries", async () => {
      const messages = [
        {
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "paged-question",
              name: "ask_user_question",
              arguments: { question: "审批人？", default: "张三" },
            },
          ],
        },
        {
          role: "toolResult",
          toolCallId: "paged-question",
          toolName: "ask_user_question",
          content: [{ type: "text", text: "" }],
          details: { status: "answered", answer: "李四" },
          isError: false,
        },
      ];
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockReturnValue(messages);

      ws.trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "latest-question-page",
              type: "get_messages",
              direction: "latest",
              limit: 1,
            },
          }),
        ),
      );
      await new Promise(resolve => setTimeout(resolve, 10));
      const latest = (ws.send as ReturnType<typeof vi.fn>).mock.calls
        .map(([message]) => JSON.parse(message as string))
        .find(message => message.payload?.id === "latest-question-page");

      ws.trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "older-question-page",
              type: "get_messages",
              direction: "older",
              cursor: latest.payload.data.oldestCursor,
              limit: 1,
            },
          }),
        ),
      );
      await new Promise(resolve => setTimeout(resolve, 10));
      const older = (ws.send as ReturnType<typeof vi.fn>).mock.calls
        .map(([message]) => JSON.parse(message as string))
        .find(message => message.payload?.id === "older-question-page");

      expect(older.payload.data.messages[0].content[0]).toMatchObject({
        id: "paged-question",
        questionState: "answered",
      });
    });

    it("replays stopped questions as cancelled without matching incidental sentinel text", async () => {
      const messages = [
        {
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "stopped-question",
              name: "ask_user_question",
              arguments: { question: "请假原因？", default: "个人事务" },
            },
            {
              type: "toolCall",
              id: "invalid-question",
              name: "ask_user_question",
              arguments: { question: "审批人？", default: "张三" },
            },
            {
              type: "toolCall",
              id: "validation-terminal-question",
              name: "ask_user_question",
              arguments: { title: "请假申请", questions: "[" },
            },
          ],
        },
        {
          role: "toolResult",
          toolCallId: "stopped-question",
          toolName: "ask_user_question",
          content: [{ type: "text", text: "Question was aborted" }],
          isError: true,
        },
        {
          role: "toolResult",
          toolCallId: "invalid-question",
          toolName: "ask_user_question",
          content: [
            {
              type: "text",
              text: "Validation mentioned QUESTION_PRESENTATION_FAILED but did not return that code",
            },
          ],
          isError: true,
        },
        {
          role: "toolResult",
          toolCallId: "validation-terminal-question",
          toolName: "ask_user_question",
          content: [
            {
              type: "text",
              text: "QUESTION_VALIDATION_FAILED: repeated invalid calls",
            },
          ],
          isError: true,
        },
      ];
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockReturnValue(messages);

      ws.trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: { id: "stopped-question-page", type: "get_messages" },
          }),
        ),
      );
      await new Promise(resolve => setTimeout(resolve, 10));
      const response = (ws.send as ReturnType<typeof vi.fn>).mock.calls
        .map(([message]) => JSON.parse(message as string))
        .find(message => message.payload?.id === "stopped-question-page");

      expect(response.payload.data.messages[0].content[0]).toMatchObject({
        id: "stopped-question",
        questionState: "cancelled",
      });
      expect(response.payload.data.messages[0].content[1]).toMatchObject({
        id: "invalid-question",
        questionState: "invalid",
      });
      expect(response.payload.data.messages[0].content[2]).toMatchObject({
        id: "validation-terminal-question",
        questionState: "terminal_failure",
      });
    });

    it("defaults transcript pages to the latest 80 messages", async () => {
      const messages = Array.from({ length: 95 }, (_, index) => ({
        role: "user",
        content: `message ${index + 1}`,
      }));
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockReturnValue(messages);
      (
        context.state.sessionManager.getEntries as ReturnType<typeof vi.fn>
      ).mockReturnValue(messages);

      const command: RpcCommand = { id: "cmd-default-page", type: "get_messages" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(true);
      expect(response.payload.data.messages).toHaveLength(80);
      expect(response.payload.data.messages[0].content).toBe("message 16");
      expect(response.payload.data.messages.at(-1).content).toBe("message 95");
      expect(response.payload.data.hasOlder).toBe(true);
    });

    it("includes compaction and model changes in transcript pages", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-compact-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendModelChange("openai", "gpt-5");
      sessionManager.appendMessage({
        role: "user",
        content: "Summarize the repo",
        timestamp: Date.now(),
      } as any);
      const firstKeptEntryId = sessionManager.getLeafId();
      if (!firstKeptEntryId) {
        throw new Error("expected a first kept entry id");
      }
      sessionManager.appendCompaction(
        "Kept the repo summary and pending fixes.",
        firstKeptEntryId,
        18800,
      );
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }

      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockImplementation(() => sessionManager.getBranch());

      const command: RpcCommand = {
        id: "cmd-compact",
        type: "get_messages",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);
      expect(response.payload.success).toBe(true);
      expect(response.payload.data.messages).toEqual([
        expect.objectContaining({
          role: "system",
          content: [
            {
              type: "model_change",
              provider: "openai",
              modelId: "gpt-5",
            },
          ],
        }),
        expect.objectContaining({
          role: "user",
          content: "Summarize the repo",
        }),
        expect.objectContaining({
          role: "system",
          content: [
            {
              type: "compaction",
              summary: "Kept the repo summary and pending fixes.",
              tokensBefore: 18800,
              firstKeptEntryId,
            },
          ],
        }),
      ]);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("omits session title metadata from transcript pages", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-title-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendSessionInfo("Inspect terminal-log-view.ts");
      sessionManager.appendMessage({
        role: "user",
        content: "Inspect terminal-log-view.ts",
        timestamp: Date.now(),
      } as any);
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }

      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockImplementation(() => sessionManager.getBranch());

      const command: RpcCommand = {
        id: "cmd-session-title",
        type: "get_messages",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);
      expect(response.payload.success).toBe(true);
      expect(response.payload.data.messages).toEqual([
        expect.objectContaining({
          role: "user",
          content: "Inspect terminal-log-view.ts",
        }),
      ]);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("hides bootstrap model and thinking entries for an empty session", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-empty-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendModelChange("openai", "gpt-5");
      sessionManager.appendThinkingLevelChange("high");
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }

      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockImplementation(() => sessionManager.getBranch());

      const command: RpcCommand = {
        id: "cmd-empty",
        type: "get_messages",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);
      expect(response.payload.success).toBe(true);
      expect(response.payload.data.messages).toEqual([]);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("should handle get_commands command", async () => {
      const command: RpcCommand = { id: "cmd-1", type: "get_commands" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.type).toBe("response");
      expect(response.payload.command).toBe("get_commands");
      expect(response.payload.success).toBe(true);
      expect(response.payload.data.commands).toHaveLength(1);
      expect(response.payload.data.commands[0]).toHaveProperty("name", "test");
      expect(response.payload.data.commands[0]).toHaveProperty(
        "source",
        "prompt",
      );
    });

    it("returns no commands or hidden discovery work when disabled", async () => {
      adapter.dispose();
      adapter = new BridgeRpcAdapter(
        client,
        message => ws.send(JSON.stringify(message)),
        context,
        DEFAULT_BRIDGE_CONFIG,
        eventBus,
        emitEvent as any,
        uploadRegistry as any,
      );
      const getCommands = context.actions.getCommands as ReturnType<
        typeof vi.fn
      >;
      getCommands.mockClear();

      ws.trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: { id: "cmd-disabled", type: "get_commands" },
          }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const response = JSON.parse(sendCalls[sendCalls.length - 1][0] as string);
      expect(response.payload.data.commands).toEqual([]);
      expect(getCommands).not.toHaveBeenCalled();
    });

    it("should handle set_model command with valid model", async () => {
      const command: RpcCommand = {
        id: "cmd-1",
        type: "set_model",
        provider: "openai",
        modelId: "gpt-4",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      expect(context.state.getAvailableModels).toHaveBeenCalled();
      expect(context.actions.setModel).toHaveBeenCalled();

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(true);
    });

    it("should handle set_model command with invalid model", async () => {
      const command: RpcCommand = {
        id: "cmd-1",
        type: "set_model",
        provider: "unknown",
        modelId: "unknown",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(false);
      expect(response.payload.error).toContain("Model not found");
    });

    it("should return error for unsupported commands", async () => {
      const command: RpcCommand = { id: "cmd-1", type: "bash", command: "ls" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(false);
      expect(response.payload.error).toContain("not supported via bridge");
    });

    it("should emit command_error event on command dispatch failure", async () => {
      // The prompt handler auto-creates a session. If that fails, the
      // error surfaces as a command_error event.
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-err-"));
      const sessionFile = path.join(tmpDir, "session.jsonl");
      fs.writeFileSync(
        sessionFile,
        JSON.stringify({
          type: "session",
          version: 3,
          id: "err-session",
          timestamp: new Date().toISOString(),
          cwd: tmpDir,
        }),
      );
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);

      createAgentSessionMock.mockRejectedValue(new Error("Dispatch failed"));

      const command: RpcCommand = {
        id: "cmd-1",
        type: "prompt",
        message: "Hello",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      expect(emitEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "command_error",
          client,
          commandType: "prompt",
          correlationId: "cmd-1",
          error: "Dispatch failed",
        }),
      );

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("ends the active turn when a detached prompt rejects asynchronously", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-prompt-fail-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: [{ type: "text", text: "Initial" }],
        timestamp: Date.now(),
      });
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) throw new Error("session file was not created");
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);

      createAgentSessionMock.mockResolvedValue({
        session: {
          sessionFile,
          sessionId: sessionManager.getSessionId(),
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: vi.fn().mockReturnValue(() => {}),
          prompt: vi.fn().mockRejectedValue(new Error("Session write failed")),
          sessionManager,
        },
      });

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "switch-prompt-fail",
              type: "switch_session",
              sessionPath: sessionFile,
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 10));

      ws.send.mockClear();
      emitEvent.mockClear();

      const command: RpcCommand = {
        id: "cmd-prompt-fail",
        type: "prompt",
        message: "Hello",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 30));

      expect(emitEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "command_error",
          client,
          commandType: "prompt",
          correlationId: "cmd-prompt-fail",
          error: "Session write failed",
        }),
      );
      expect(
        ws.send.mock.calls
          .map(call => JSON.parse(call[0] as string))
          .some(
            call =>
              call.type === "event" &&
              call.payload.type === "command_error" &&
              call.payload.commandType === "prompt" &&
              call.payload.correlationId === "cmd-prompt-fail" &&
              call.payload.error === "Session write failed",
          ),
      ).toBe(true);
      expect(
        ws.send.mock.calls
          .map(call => JSON.parse(call[0] as string))
          .some(
            call =>
              call.type === "event" &&
              call.payload.type === "agent_end" &&
              call.payload.sessionPath === sessionFile,
          ),
      ).toBe(true);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });
  });

  describe("extension UI routing", () => {
    it.each([
      ["heimdall: 6 guards active", "info"],
      [
        "heimdall sandbox: not supported on darwin (Linux only)",
        "warning",
      ],
      ["heimdall: blocked risky kubectl command", "warning"],
    ] as const)("should suppress Heimdall notification %j", (message, notifyType) => {
      const uiContext = adapter.createExtensionUIContext();
      ws.send.mockClear();

      uiContext.notify(message, notifyType);

      expect(ws.send).not.toHaveBeenCalled();
    });

    it.each([
      ["Extension loaded", "info"],
      ["heimdallish extension loaded", "warning"],
    ] as const)("should preserve non-Heimdall notification %j", (message, notifyType) => {
      const uiContext = adapter.createExtensionUIContext();
      ws.send.mockClear();

      uiContext.notify(message, notifyType);

      expect(JSON.parse(ws.send.mock.calls[0][0])).toEqual(
        expect.objectContaining({
          type: "extension_ui_request",
          payload: expect.objectContaining({
            method: "notify",
            message,
            notifyType,
          }),
        }),
      );
    });

    it("should support extension status formatting in browser UI mode", () => {
      const uiContext = adapter.createExtensionUIContext();

      expect(uiContext.theme.fg("accent", "guarded")).toBe("guarded");
    });

    it("should send fire-and-forget UI requests to the client", () => {
      const uiContext = adapter.createExtensionUIContext();

      uiContext.setTitle("Bridge UI");
      uiContext.setEditorText("draft text");

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );

      expect(sendCalls).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            type: "extension_ui_request",
            payload: expect.objectContaining({
              method: "setTitle",
              title: "Bridge UI",
            }),
          }),
          expect.objectContaining({
            type: "extension_ui_request",
            payload: expect.objectContaining({
              method: "set_editor_text",
              text: "draft text",
            }),
          }),
        ]),
      );
    });

    it("should send UI request and wait for response", async () => {
      const uiContext = adapter.createExtensionUIContext();

      // Start select request
      const selectPromise = uiContext.select("Choose one", ["a", "b", "c"]);

      // Should have sent a UI request
      await new Promise(r => setTimeout(r, 10));
      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = JSON.parse(sendCalls[sendCalls.length - 1][0] as string);

      expect(lastCall.type).toBe("extension_ui_request");
      expect(lastCall.payload.method).toBe("select");
      expect(lastCall.payload).toHaveProperty("id");

      // Simulate client response
      const requestId = lastCall.payload.id;
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "extension_ui_response",
            payload: {
              type: "extension_ui_response",
              id: requestId,
              value: "a",
            } as RpcExtensionUIResponse,
          }),
        ),
      );

      const result = await selectPromise;
      expect(result).toBe("a");
    });

    it("should handle UI request timeout", async () => {
      const shortTimeoutConfig = {
        ...DEFAULT_BRIDGE_CONFIG,
        uiRequestTimeout: 50,
      };
      const shortAdapter = new BridgeRpcAdapter(
        client,
        message => ws.send(JSON.stringify(message)),
        context,
        shortTimeoutConfig,
        eventBus,
        emitEvent as any,
        uploadRegistry as any,
      );

      const uiContext = shortAdapter.createExtensionUIContext();

      // Start confirm request
      const confirmPromise = uiContext.confirm(
        "Are you sure?",
        "This will delete everything",
      );

      // Wait for timeout
      const result = await confirmPromise;

      // Should return default value (false for confirm)
      expect(result).toBe(false);
    });

    it("should handle UI response for unknown request gracefully", async () => {
      // Send response for non-existent request
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "extension_ui_response",
            payload: {
              type: "extension_ui_response",
              id: "unknown-id",
              value: "test",
            } as RpcExtensionUIResponse,
          }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      // Should not throw, just log a warning
      expect(ws.send).not.toHaveBeenCalledWith(
        expect.stringContaining("error"),
      );
    });

    it("should handle cancelled UI response", async () => {
      const uiContext = adapter.createExtensionUIContext();

      // Start input request
      const inputPromise = uiContext.input("Enter name");

      await new Promise(r => setTimeout(r, 10));
      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = JSON.parse(sendCalls[sendCalls.length - 1][0] as string);
      const requestId = lastCall.payload.id;

      // Send cancelled response
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "extension_ui_response",
            payload: {
              type: "extension_ui_response",
              id: requestId,
              cancelled: true,
            } as RpcExtensionUIResponse,
          }),
        ),
      );

      const result = await inputPromise;
      expect(result).toBeUndefined();
    });
  });

  describe("event fan-out", () => {
    it("sends an initial transcript snapshot to the client", () => {
      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const firstCall = JSON.parse(sendCalls[0][0] as string);

      expect(firstCall.type).toBe("event");
      expect(firstCall.payload.type).toBe("transcript_snapshot");
      expect(firstCall.payload.messages).toEqual([
        {
          transcriptKey: "snapshot:0",
          role: "user",
          content: "Hello",
          id: undefined,
          timestamp: undefined,
        },
      ]);
      expect(firstCall.payload.hasOlder).toBe(false);
      expect(firstCall.payload.hasNewer).toBe(false);
    });

    it("pushes initial session stats to the client", async () => {
      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const statsEvent = sendCalls.find(
        call => call.type === "event" && call.payload.type === "session_stats",
      );

      expect(statsEvent?.payload).toMatchObject({
        type: "session_stats",
        stats: {
          tokens: 1000,
          contextWindow: 8000,
          percent: 12.5,
          messageCount: 1,
          cost: 0,
          inputTokens: 0,
          outputTokens: 0,
          cacheReadTokens: 0,
          cacheWriteTokens: 0,
        },
      });
    });

    it("routes live transcript updates and pushed session stats directly to the client", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_start",
        message: { role: "assistant", content: "Hi" },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: "Hi there",
        },
      });

      await new Promise(r => setTimeout(r, 250));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );

      expect(sendCalls).toHaveLength(2);
      expect(sendCalls[0].type).toBe("event");
      expect(sendCalls[0].payload).toMatchObject({
        type: "transcript_start",
        message: {
          transcriptKey: "live:1",
          role: "assistant",
          content: [],
        },
      });
      expect(sendCalls[0].payload.treeEntries).toEqual(expect.any(Array));
      expect(sendCalls[1].payload).toMatchObject({
        type: "transcript_delta",
        transcriptKey: "live:1",
        messageId: "assistant-1",
        role: "assistant",
        contentIndex: 0,
        blockType: "text",
        delta: "Hi there",
      });
      expect(sendCalls[1].payload.treeEntries).toBeUndefined();
    });

    it("streams assistant message deltas without resending full messages", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_start",
        message: { id: "assistant-1", role: "assistant", content: [] },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [{ type: "text", text: "Hel" }],
        },
        assistantMessageEvent: {
          type: "text_delta",
          contentIndex: 0,
          delta: "Hel",
        },
      });

      await new Promise(r => setTimeout(r, 250));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );

      expect(sendCalls).toHaveLength(2);
      expect(sendCalls[0].payload).toMatchObject({
        type: "transcript_start",
        message: {
          transcriptKey: "live:1",
          id: "assistant-1",
          role: "assistant",
          content: [],
        },
      });
      expect(sendCalls[1].payload).toMatchObject({
        type: "transcript_delta",
        transcriptKey: "live:1",
        messageId: "assistant-1",
        role: "assistant",
        contentIndex: 0,
        blockType: "text",
        delta: "Hel",
      });
      expect(sendCalls[1].payload.message).toBeUndefined();
      expect(sendCalls[1].payload.treeEntries).toBeUndefined();
    });

    it("includes tool metadata in streamed tool call deltas", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_start",
        message: { id: "assistant-1", role: "assistant", content: [] },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "tool-1",
              name: "read",
              arguments: '{"path":"a.txt"}',
            },
          ],
        },
        assistantMessageEvent: {
          type: "toolcall_delta",
          contentIndex: 0,
          delta: '{"path":"a.txt"}',
        },
      });

      await new Promise(r => setTimeout(r, 250));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );

      expect(sendCalls).toHaveLength(2);
      expect(sendCalls[1].payload).toMatchObject({
        type: "transcript_delta",
        transcriptKey: "live:1",
        messageId: "assistant-1",
        role: "assistant",
        contentIndex: 0,
        blockType: "toolCall",
        delta: '{"path":"a.txt"}',
        toolCallId: "tool-1",
        toolName: "read",
      });
    });

    it("preserves streamed tool call arguments when final update is sparse", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const argumentsJson = JSON.stringify({
        title: "公章使用申请",
        questions: JSON.stringify([
          {
            id: "seal_id",
            question: "印章类型？",
            options: ["公章", "合同章"],
            default: "公章",
          },
          {
            id: "reason",
            question: "用章事由？",
            default: "签署合同",
          },
        ]),
      });

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_start",
        message: { id: "assistant-1", role: "assistant", content: [] },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "tool-1",
              name: "ask_user_question",
              arguments: "",
            },
          ],
        },
        assistantMessageEvent: {
          type: "toolcall_delta",
          contentIndex: 0,
          delta: argumentsJson,
        },
      });
      handler?.({
        type: "message_end",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "tool-1",
              name: "ask_user_question",
              arguments: "",
            },
          ],
        },
      });

      await new Promise(r => setTimeout(r, 250));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const finalUpsert = lastTranscriptUpsert(sendCalls);

      expect(finalUpsert?.payload.message.content[0]).toMatchObject({
        type: "toolCall",
        id: "tool-1",
        name: "ask_user_question",
        arguments: argumentsJson,
        questionRequest: {
          batch: true,
          title: "公章使用申请",
          questions: [
            expect.objectContaining({ id: "seal_id", kind: "single" }),
            expect.objectContaining({ id: "reason", kind: "text" }),
          ],
        },
      });
    });

    it("preserves streamed tool call arguments when final update has empty object arguments", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_start",
        message: { id: "assistant-1", role: "assistant", content: [] },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "tool-1",
              name: "ask_user_question",
              arguments: "",
            },
          ],
        },
        assistantMessageEvent: {
          type: "toolcall_delta",
          contentIndex: 0,
          delta: '{"question":"请填写说明","inputType":"textarea","default":"默认内容"}',
        },
      });
      handler?.({
        type: "message_end",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "tool-1",
              name: "ask_user_question",
              arguments: {},
            },
          ],
        },
      });

      await new Promise(r => setTimeout(r, 250));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const finalUpsert = lastTranscriptUpsert(sendCalls);

      expect(finalUpsert?.payload.message.content[0]).toMatchObject({
        type: "toolCall",
        id: "tool-1",
        name: "ask_user_question",
        arguments: '{"question":"请填写说明","inputType":"textarea","default":"默认内容"}',
      });
    });

    it("keeps the previous question card when another question tool streams at the same index", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_start",
        message: { id: "assistant-1", role: "assistant", content: [] },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "tool-1",
              name: "ask_user_question",
              arguments: "",
            },
          ],
        },
        assistantMessageEvent: {
          type: "toolcall_delta",
          contentIndex: 0,
          delta: '{"question":"选择配置","options":["A","B"],"default":"A"}',
        },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "tool-2",
              name: "ask_user_question",
              arguments: "",
            },
          ],
        },
        assistantMessageEvent: {
          type: "toolcall_delta",
          contentIndex: 0,
          delta: '{"question":"请填写说明","inputType":"textarea","default":"默认内容"}',
        },
      });
      handler?.({
        type: "message_end",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "tool-2",
              name: "ask_user_question",
              arguments: "",
            },
          ],
        },
      });

      await new Promise(r => setTimeout(r, 250));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const finalUpsert = lastTranscriptUpsert(sendCalls);

      expect(finalUpsert?.payload.message.content).toEqual([
        {
          type: "toolCall",
          id: "tool-1",
          name: "ask_user_question",
          arguments: '{"question":"选择配置","options":["A","B"],"default":"A"}',
          questionRequest: expect.objectContaining({
            batch: false,
            kind: "single",
            question: "选择配置",
          }),
        },
        {
          type: "toolCall",
          id: "tool-2",
          name: "ask_user_question",
          arguments: '{"question":"请填写说明","inputType":"textarea","default":"默认内容"}',
          questionRequest: expect.objectContaining({
            batch: false,
            kind: "text",
            question: "请填写说明",
          }),
        },
      ]);
    });

    it("includes tool metadata in synthesized tool call deltas", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_start",
        message: { id: "assistant-1", role: "assistant", content: [] },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "tool-1",
              name: "read",
              arguments: '{"path":"a.txt"}',
            },
          ],
        },
      });

      await new Promise(r => setTimeout(r, 250));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );

      expect(sendCalls).toHaveLength(2);
      expect(sendCalls[1].payload).toMatchObject({
        type: "transcript_delta",
        transcriptKey: "live:1",
        messageId: "assistant-1",
        role: "assistant",
        contentIndex: 0,
        blockType: "toolCall",
        delta: '{"path":"a.txt"}',
        toolCallId: "tool-1",
        toolName: "read",
      });
    });

    it("upserts message updates that only add tool results and follow-up questions", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_start",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "approvers-1",
              name: "ask_user_question",
              arguments: {
                title: "请假申请",
                questions: [
                  {
                    id: "approver1",
                    question: "审批人 1",
                    default: "张三",
                  },
                  {
                    id: "approver2",
                    question: "审批人 2",
                    default: "李四",
                  },
                ],
              },
            },
          ],
        },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "approvers-1",
              name: "ask_user_question",
              arguments: {
                title: "请假申请",
                questions: [
                  {
                    id: "approver1",
                    question: "审批人 1",
                    default: "张三",
                  },
                  {
                    id: "approver2",
                    question: "审批人 2",
                    default: "李四",
                  },
                ],
              },
            },
            {
              type: "toolResult",
              text: "",
              details: {
                status: "answered",
                answer: { approver1: "张三", approver2: "李四" },
              },
              isError: false,
            },
            {
              type: "toolCall",
              id: "confirm-1",
              name: "ask_user_question",
              arguments: {
                confirm: true,
              },
            },
          ],
        },
      });

      await new Promise(r => setTimeout(r, 250));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const finalUpsert = lastTranscriptUpsert(sendCalls);

      expect(finalUpsert?.payload.message.content).toEqual([
        {
          type: "toolCall",
          id: "approvers-1",
          name: "ask_user_question",
          arguments: {
            title: "请假申请",
            questions: [
              {
                id: "approver1",
                question: "审批人 1",
                default: "张三",
              },
              {
                id: "approver2",
                question: "审批人 2",
                default: "李四",
              },
            ],
          },
          questionRequest: {
            batch: true,
            title: "请假申请",
            questions: [
              {
                id: "approver1",
                kind: "text",
                question: "审批人 1",
                default: "张三",
              },
              {
                id: "approver2",
                kind: "text",
                question: "审批人 2",
                default: "李四",
              },
            ],
          },
          questionState: "answered",
        },
        {
          type: "toolResult",
          text: "",
          details: {
            status: "answered",
            answer: { approver1: "张三", approver2: "李四" },
          },
          isError: false,
        },
        {
          type: "toolCall",
          id: "confirm-1",
          name: "ask_user_question",
          arguments: {
            confirm: true,
          },
          questionRequest: {
            batch: false,
            kind: "confirm",
            id: "confirmation",
            title: "请假申请确认",
            confirmationOfToolCallId: "approvers-1",
            questions: [
              {
                id: "approver1",
                kind: "text",
                question: "审批人 1",
                default: "张三",
              },
              {
                id: "approver2",
                kind: "text",
                question: "审批人 2",
                default: "李四",
              },
            ],
            answer: { approver1: "张三", approver2: "李四" },
          },
        },
      ]);
    });

    it("projects live confirmation cards with redundant legacy fields after a submitted grouped form", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;
      const formMessage = {
        id: "assistant-form",
        role: "assistant",
        content: [
          {
            type: "toolCall",
            id: "form-call",
            name: "ask_user_question",
            arguments: {
              title: "公章使用申请表",
              questions: JSON.stringify([
                {
                  id: "seal_id",
                  question: "请选择印章",
                  default: "公司章",
                },
                {
                  id: "reason",
                  question: "请描述具体用途",
                  default: "用于公司业务文件盖章",
                },
              ]),
            },
          },
        ],
      };
      const resultMessage = {
        id: "form-result",
        role: "toolResult",
        toolCallId: "form-call",
        toolName: "ask_user_question",
        content: [{ type: "text", text: "answered" }],
        details: {
          status: "answered",
          answer: {
            seal_id: "公司章",
            reason: "用于公司业务文件盖章",
          },
        },
        isError: false,
      };
      const confirmMessage = {
        id: "assistant-confirm",
        role: "assistant",
        content: [
          {
            type: "toolCall",
            id: "confirm-call",
            name: "ask_user_question",
            arguments: {
              confirm: true,
              question: "是否确认提交？",
            },
          },
        ],
      };

      for (const message of [formMessage, resultMessage, confirmMessage]) {
        handler?.({ type: "message_start", message });
        handler?.({ type: "message_end", message });
      }

      await new Promise(r => setTimeout(r, 250));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const confirmUpsert = [...sendCalls].reverse().find(
        (call: any) =>
          call.payload?.type === "transcript_upsert" &&
          call.payload.message?.id === "assistant-confirm",
      );

      expect(confirmUpsert?.payload.message.content[0]).toMatchObject({
        id: "confirm-call",
        name: "ask_user_question",
        questionRequest: {
          batch: false,
          kind: "confirm",
          title: "公章使用申请表确认",
          confirmationOfToolCallId: "form-call",
          answer: {
            seal_id: "公司章",
            reason: "用于公司业务文件盖章",
          },
        },
      });
    });

    it("keeps recoverable question failures retryable but aborts a terminal failure", async () => {
      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_end",
        message: {
          id: "question-retry-result",
          role: "toolResult",
          toolCallId: "question-retry",
          toolName: "ask_user_question",
          content: [
            {
              type: "text",
              text: "QUESTION_PRESENTATION_TIMEOUT: retry the native tool call",
            },
          ],
          isError: true,
        },
      });

      expect(context.actions.abort).not.toHaveBeenCalled();

      handler?.({
        type: "message_end",
        message: {
          id: "question-terminal-result",
          role: "toolResult",
          toolCallId: "question-terminal",
          toolName: "ask_user_question",
          content: [
            {
              type: "text",
              text: "QUESTION_PRESENTATION_FAILED: Dano could not display the question card",
            },
          ],
          isError: true,
        },
      });

      expect(context.actions.abort).toHaveBeenCalledTimes(1);
    });

    it("aborts the active detached session after a terminal question failure", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "dano-terminal-question-"),
      );
      const abort = vi.fn().mockResolvedValue(undefined);
      const subscribe = vi.fn().mockReturnValue(() => {});
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(path.join(tmpDir, "live.jsonl"));
      (
        context.state.sessionManager.getCwd as ReturnType<typeof vi.fn>
      ).mockReturnValue(tmpDir);
      createAgentSessionMock.mockResolvedValue({
        session: {
          sessionFile: undefined,
          sessionId: "terminal-question-session",
          isStreaming: true,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe,
          prompt: vi.fn().mockResolvedValue(undefined),
          abort,
          sessionManager: {
            getSessionFile: vi.fn().mockReturnValue(undefined),
            getSessionId: vi.fn().mockReturnValue("terminal-question-session"),
            getEntries: vi.fn().mockReturnValue([]),
            getBranch: vi.fn().mockReturnValue([]),
            getCwd: vi.fn().mockReturnValue(tmpDir),
            getLeafId: vi.fn().mockReturnValue(null),
            getTree: vi.fn().mockReturnValue([]),
          },
        },
      });

      ws.trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "terminal-question-prompt",
              type: "prompt",
              message: "酒店申请",
            },
          }),
        ),
      );
      await new Promise(resolve => setTimeout(resolve, 20));

      const handler = subscribe.mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;
      handler?.({
        type: "message_end",
        message: {
          id: "question-terminal-result",
          role: "toolResult",
          toolCallId: "question-terminal",
          toolName: "ask_user_question",
          content: [
            {
              type: "text",
              text: "QUESTION_VALIDATION_FAILED: repeated invalid calls",
            },
          ],
          isError: true,
        },
      });
      await Promise.resolve();

      expect(abort).toHaveBeenCalledTimes(1);
      expect(context.actions.abort).not.toHaveBeenCalled();
      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("coalesces consecutive transcript deltas for the same block", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_start",
        message: { id: "assistant-1", role: "assistant", content: [] },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [{ type: "text", text: "He" }],
        },
        assistantMessageEvent: {
          type: "text_delta",
          contentIndex: 0,
          delta: "He",
        },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [{ type: "text", text: "Hello" }],
        },
        assistantMessageEvent: {
          type: "text_delta",
          contentIndex: 0,
          delta: "llo",
        },
      });

      await new Promise(r => setTimeout(r, 250));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );

      expect(sendCalls).toHaveLength(2);
      expect(sendCalls[1].payload).toMatchObject({
        type: "transcript_delta",
        transcriptKey: "live:1",
        messageId: "assistant-1",
        role: "assistant",
        contentIndex: 0,
        blockType: "text",
        delta: "Hello",
      });
    });

    it("prefers assistant delta events over synthesized transcript diffs", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_start",
        message: { id: "assistant-1", role: "assistant", content: [] },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [{ type: "text", text: "Hello\n\nHello world" }],
        },
        assistantMessageEvent: {
          type: "text_delta",
          contentIndex: 0,
          delta: "Hello world",
        },
      });

      await new Promise(r => setTimeout(r, 250));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );

      expect(sendCalls).toHaveLength(2);
      expect(sendCalls[1].payload).toMatchObject({
        type: "transcript_delta",
        transcriptKey: "live:1",
        messageId: "assistant-1",
        role: "assistant",
        contentIndex: 0,
        blockType: "text",
        delta: "Hello world",
      });
    });

    it("ignores late transcript deltas after message_end", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "message_start",
        message: { id: "assistant-1", role: "assistant", content: [] },
      });
      handler?.({
        type: "message_end",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [{ type: "text", text: "Hello" }],
        },
      });
      handler?.({
        type: "message_update",
        message: {
          id: "assistant-1",
          role: "assistant",
          content: [{ type: "text", text: "Hello there" }],
        },
        assistantMessageEvent: {
          type: "text_delta",
          contentIndex: 0,
          delta: " there",
        },
      });

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const transcriptCalls = sendCalls.filter(
        call =>
          call.type === "event" &&
          [
            "transcript_start",
            "transcript_upsert",
            "transcript_delta",
          ].includes(call.payload?.type),
      );

      expect(transcriptCalls).toHaveLength(2);
      expect(transcriptCalls[0].payload).toMatchObject({
        type: "transcript_start",
        message: {
          transcriptKey: "live:1",
          id: "assistant-1",
          role: "assistant",
          content: [],
        },
      });
      expect(transcriptCalls[1].payload).toMatchObject({
        type: "transcript_upsert",
        message: {
          transcriptKey: "live:1",
          id: "assistant-1",
          role: "assistant",
          content: [{ type: "text", text: "Hello" }],
        },
      });
      expect(
        transcriptCalls.some(call => call.payload?.type === "transcript_delta"),
      ).toBe(false);
    });

    it("shapes agent_start events explicitly", () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({ type: "agent_start", leaked: true });

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );

      expect(sendCalls).toHaveLength(1);
      expect(sendCalls[0].payload).toEqual({
        type: "agent_start",
        sessionPath: "/path/to/session.json",
      });
    });

    it("pushes shaped agent_end events and session stats", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "agent_end",
        leaked: true,
        messages: [
          {
            role: "assistant",
            content: [{ type: "text", text: "Done." }],
            api: "openai-responses",
            provider: "openai",
            model: "gpt-5",
            usage: {
              input: 10,
              output: 4,
              cacheRead: 0,
              cacheWrite: 0,
              totalTokens: 14,
              cost: {
                input: 0,
                output: 0,
                cacheRead: 0,
                cacheWrite: 0,
                total: 0,
              },
            },
            stopReason: "stop",
            timestamp: 123,
          },
        ],
      });

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );

      expect(sendCalls).toHaveLength(2);
      expect(sendCalls[0].payload).toEqual({
        type: "agent_end",
        sessionPath: "/path/to/session.json",
        messages: [
          {
            role: "assistant",
            content: [{ type: "text", text: "Done." }],
            api: "openai-responses",
            provider: "openai",
            model: "gpt-5",
            usage: {
              input: 10,
              output: 4,
              cacheRead: 0,
              cacheWrite: 0,
              totalTokens: 14,
              cost: {
                input: 0,
                output: 0,
                cacheRead: 0,
                cacheWrite: 0,
                total: 0,
              },
            },
            stopReason: "stop",
            timestamp: 123,
          },
        ],
      });
      expect(sendCalls[1].payload).toMatchObject({
        type: "session_stats",
        stats: {
          tokens: 1000,
          contextWindow: 8000,
          percent: 12.5,
        },
      });
    });

    it("sanitizes model failures in transcript and terminal events", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;
      const failedMessage = {
        id: "assistant-timeout",
        role: "assistant",
        content: [],
        api: "openai-completions",
        provider: "private-provider",
        model: "private-model",
        usage: {
          input: 0,
          output: 0,
          cacheRead: 0,
          cacheWrite: 0,
          totalTokens: 0,
          cost: {
            input: 0,
            output: 0,
            cacheRead: 0,
            cacheWrite: 0,
            total: 0,
          },
        },
        stopReason: "error",
        errorMessage: "Request timed out with Authorization: Bearer secret",
        timestamp: 123,
      };

      handler?.({ type: "message_end", message: failedMessage });
      handler?.({ type: "agent_end", messages: [failedMessage] });
      await new Promise(r => setTimeout(r, 10));

      const payloads = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string).payload,
      );
      expect(
        payloads.find(payload => payload.type === "transcript_upsert")?.message
          .errorMessage,
      ).toBe("DANO_LLM_TIMEOUT");
      expect(
        payloads.find(payload => payload.type === "agent_end")?.messages[0]
          .errorMessage,
      ).toBe("DANO_LLM_TIMEOUT");
      expect(JSON.stringify(payloads)).not.toContain("Bearer secret");
    });

    it("keeps retry attempts non-terminal and publishes only the final error", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;
      const failedMessage = (id: string) => ({
        id,
        role: "assistant",
        content: [],
        api: "openai-completions",
        provider: "test-provider",
        model: "test-model",
        usage: {
          input: 0,
          output: 0,
          cacheRead: 0,
          cacheWrite: 0,
          totalTokens: 0,
          cost: {
            input: 0,
            output: 0,
            cacheRead: 0,
            cacheWrite: 0,
            total: 0,
          },
        },
        stopReason: "error",
        errorMessage: "Request timed out.",
        timestamp: 123,
      });

      const firstAttempt = failedMessage("assistant-timeout-1");
      handler?.({ type: "message_start", message: firstAttempt });
      handler?.({ type: "message_end", message: firstAttempt });
      handler?.({
        type: "agent_end",
        messages: [firstAttempt],
        willRetry: true,
      });
      expect(ws.send).not.toHaveBeenCalled();
      handler?.({
        type: "auto_retry_start",
        attempt: 1,
        maxAttempts: 10,
        delayMs: 2000,
        errorMessage: "Request timed out with Bearer secret",
      });

      const finalAttempt = failedMessage("assistant-timeout-2");
      handler?.({ type: "message_start", message: finalAttempt });
      handler?.({ type: "message_end", message: finalAttempt });
      handler?.({
        type: "agent_end",
        messages: [finalAttempt],
        willRetry: false,
      });
      await new Promise(r => setTimeout(r, 10));

      const payloads = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string).payload,
      );
      expect(
        payloads.filter(payload => payload.type === "auto_retry_start"),
      ).toEqual([
        {
          type: "auto_retry_start",
          sessionPath: "/path/to/session.json",
          attempt: 1,
          maxAttempts: 10,
          delayMs: 2000,
        },
      ]);
      expect(JSON.stringify(payloads)).not.toContain("Bearer secret");
      expect(
        payloads.filter(payload => payload.type === "transcript_upsert"),
      ).toHaveLength(1);
      expect(
        payloads.filter(payload => payload.type === "agent_end"),
      ).toHaveLength(1);
      expect(
        payloads.find(payload => payload.type === "transcript_upsert")?.message,
      ).toMatchObject({
        id: "assistant-timeout-2",
        errorMessage: "DANO_LLM_TIMEOUT",
      });
    });

    it("shapes model_select events explicitly", async () => {
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "model_select",
        model: {
          id: "gpt-5",
          provider: "openai",
          name: "GPT-5",
          api: "openai-responses",
          baseUrl: "https://api.openai.com/v1",
          reasoning: true,
          input: ["text", "image"],
          cost: {
            input: 0,
            output: 0,
            cacheRead: 0,
            cacheWrite: 0,
          },
          contextWindow: 400000,
          maxTokens: 128000,
        },
        previousModel: {
          id: "gpt-4.1",
          provider: "openai",
          name: "GPT-4.1",
          api: "openai-responses",
          baseUrl: "https://api.openai.com/v1",
          reasoning: false,
          input: ["text"],
          cost: {
            input: 0,
            output: 0,
            cacheRead: 0,
            cacheWrite: 0,
          },
          contextWindow: 128000,
          maxTokens: 32768,
        },
        source: "set",
        leaked: true,
      });

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );

      expect(sendCalls).toHaveLength(2);
      expect(sendCalls[0].payload).toEqual({
        type: "model_select",
        model: {
          id: "gpt-5",
          provider: "openai",
          name: "GPT-5",
          api: "openai-responses",
          reasoning: true,
          contextWindow: 400000,
          maxTokens: 128000,
        },
        previousModel: {
          id: "gpt-4.1",
          provider: "openai",
          name: "GPT-4.1",
          api: "openai-responses",
          reasoning: false,
          contextWindow: 128000,
          maxTokens: 32768,
        },
        source: "set",
      });
      expect(sendCalls[1].payload).toMatchObject({
        type: "session_stats",
        stats: {
          tokens: 1000,
          contextWindow: 8000,
          percent: 12.5,
        },
      });
    });

    it("forwards selected-session compaction lifecycle events", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-compaction-events-"),
      );
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: "Initial prompt",
        timestamp: Date.now(),
      } as any);
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }

      (adapter as any).selectedSession = {
        model: undefined,
        thinkingLevel: "medium",
        isStreaming: false,
        isCompacting: true,
        steeringMode: "all",
        followUpMode: "all",
        sessionFile,
        sessionId: "selected-session",
        autoCompactionEnabled: true,
        pendingMessageCount: 0,
        sessionManager,
      };

      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      (adapter as any).handleSelectedSessionEvent({
        type: "compaction_start",
        reason: "threshold",
      });

      let sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      expect(sendCalls[0].payload).toEqual({
        type: "compaction_start",
        reason: "threshold",
      });

      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      (adapter as any).handleSelectedSessionEvent({
        type: "compaction_end",
        reason: "threshold",
        result: undefined,
        aborted: false,
        willRetry: false,
        errorMessage: "API quota exceeded",
      });

      await new Promise(r => setTimeout(r, 10));

      sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(call =>
        JSON.parse(call[0] as string),
      );
      expect(sendCalls).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            payload: expect.objectContaining({ type: "transcript_snapshot" }),
          }),
          expect.objectContaining({
            payload: {
              type: "compaction_end",
              reason: "threshold",
              result: null,
              aborted: false,
              willRetry: false,
              errorMessage: "API quota exceeded",
            },
          }),
        ]),
      );

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("refreshes the transcript after live compaction completes", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-live-compact-"),
      );
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: "Initial prompt",
        timestamp: Date.now(),
      } as any);
      const firstKeptEntryId = sessionManager.getLeafId();
      if (!firstKeptEntryId) {
        throw new Error("expected a branch leaf id");
      }
      sessionManager.appendCompaction(
        "Saved the active task before pruning history.",
        firstKeptEntryId,
        22400,
      );
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockImplementation(() => sessionManager.getBranch());

      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;

      handler?.({
        type: "session_compact",
        compactionEntry: sessionManager.getBranch().at(-1),
        fromExtension: false,
      });

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      expect(sendCalls[0].payload).toMatchObject({
        type: "transcript_snapshot",
        messages: expect.arrayContaining([
          expect.objectContaining({
            role: "system",
            content: [
              {
                type: "compaction",
                summary: "Saved the active task before pruning history.",
                tokensBefore: 22400,
                firstKeptEntryId,
              },
            ],
          }),
        ]),
      });
      expect(sendCalls[1].payload).toMatchObject({ type: "session_stats" });

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });
  });

  describe("error handling", () => {
    it("should surface transport errors", () => {
      (
        ws as unknown as { trigger: (event: string, err: Error) => void }
      ).trigger("error", new Error("Connection lost"));

      expect(emitEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "command_error",
          client,
          commandType: "transport",
        }),
      );
    });

    it("should handle JSON parse errors", async () => {
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger("message", Buffer.from("invalid json"));

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = JSON.parse(sendCalls[sendCalls.length - 1][0] as string);

      expect(lastCall.type).toBe("response");
      expect(lastCall.payload.success).toBe(false);
      expect(lastCall.payload.error).toContain("Failed to parse");
    });

    it("should handle unknown message types", async () => {
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "unknown_type" })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = JSON.parse(sendCalls[sendCalls.length - 1][0] as string);

      expect(lastCall.type).toBe("response");
      expect(lastCall.payload.success).toBe(false);
      expect(lastCall.payload.error).toContain("Unknown message type");
    });
  });

  describe("discovery commands", () => {
    it("should list sessions with the newest one first", async () => {
      const sessionDir = path.join(
        process.env.PI_WEB_SESSIONS_ROOT!,
        "--tmp--",
      );
      fs.mkdirSync(sessionDir, { recursive: true });
      const currentSessionFile = path.join(sessionDir, "current-session.jsonl");
      const olderSessionFile = path.join(sessionDir, "older-session.jsonl");
      fs.writeFileSync(
        currentSessionFile,
        [
          JSON.stringify({
            type: "session",
            id: "current-id",
            timestamp: "2025-01-02T00:00:00Z",
            cwd: "/tmp",
          }),
          JSON.stringify({
            type: "message",
            id: "current-msg-1",
            parentId: null,
            timestamp: new Date().toISOString(),
            message: {
              role: "user",
              content: "Current first prompt",
              timestamp: Date.now(),
            },
          }),
        ].join("\n") + "\n",
      );
      fs.writeFileSync(
        olderSessionFile,
        [
          JSON.stringify({
            type: "session",
            id: "older-id",
            timestamp: "2025-01-01T00:00:00Z",
            cwd: "/tmp",
          }),
          JSON.stringify({
            type: "message",
            id: "older-msg-1",
            parentId: null,
            timestamp: new Date().toISOString(),
            message: {
              role: "user",
              content: "Older first prompt",
              timestamp: Date.now(),
            },
          }),
        ].join("\n") + "\n",
      );
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(currentSessionFile);
      vi.mocked(SessionManager.listAll).mockResolvedValue([
        {
          path: currentSessionFile,
          id: "current-id",
          cwd: "/tmp",
          created: new Date("2025-01-02T00:00:00Z"),
          modified: new Date("2025-01-02T00:00:00Z"),
          messageCount: 1,
          firstMessage: "Current first prompt",
          allMessagesText: "Current first prompt",
        },
        {
          path: olderSessionFile,
          id: "older-id",
          cwd: "/tmp",
          created: new Date("2025-01-01T00:00:00Z"),
          modified: new Date("2025-01-01T00:00:00Z"),
          messageCount: 1,
          firstMessage: "Older first prompt",
          allMessagesText: "Older first prompt",
        },
      ]);

      const command: RpcCommand = {
        id: "cmd-1",
        type: "list_sessions",
        workspacePath: "/tmp",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.type).toBe("response");
      expect(response.payload.command).toBe("list_sessions");
      expect(response.payload.success).toBe(true);
      expect(response.payload.data.sessions).toEqual([
        {
          id: "current-id",
          name: "Current first prompt",
          path: currentSessionFile,
          isRunning: false,
          timestamp: "2025-01-02T00:00:00.000Z",
          updatedAt: "2025-01-02T00:00:00.000Z",
          workspaceId: "/tmp",
          workspaceName: "tmp",
          workspacePath: "/tmp",
        },
        {
          id: "older-id",
          name: "Older first prompt",
          path: olderSessionFile,
          isRunning: false,
          timestamp: "2025-01-01T00:00:00.000Z",
          updatedAt: "2025-01-01T00:00:00.000Z",
          workspaceId: "/tmp",
          workspaceName: "tmp",
          workspacePath: "/tmp",
        },
      ]);

      fs.rmSync(sessionDir, { recursive: true, force: true });
    });

    it("shows a generic label for empty sessions instead of the session filename", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-empty-"));
      const sessionDir = path.join(
        process.env.PI_WEB_SESSIONS_ROOT!,
        `--${tmpDir.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`,
      );
      fs.mkdirSync(sessionDir, { recursive: true });
      const emptySessionFile = path.join(sessionDir, "session-123.jsonl");
      fs.writeFileSync(
        emptySessionFile,
        [
          JSON.stringify({
            type: "session",
            id: "empty-id",
            timestamp: "2025-01-03T00:00:00Z",
            cwd: tmpDir,
          }),
        ].join("\n") + "\n",
      );
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(emptySessionFile);

      const command: RpcCommand = {
        id: "cmd-empty",
        type: "list_sessions",
        workspacePath: tmpDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const responseCall = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "list_sessions" &&
          call.payload.id === "cmd-empty",
      );

      expect(responseCall?.payload.success).toBe(true);
      expect(responseCall?.payload.data.sessions).toContainEqual(
        expect.objectContaining({
          id: "empty-id",
          name: "New session",
          path: emptySessionFile,
        }),
      );

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("extracts the first user text from a truncated image-backed message", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-image-"));
      const sessionDir = path.join(
        process.env.PI_WEB_SESSIONS_ROOT!,
        `--${tmpDir.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`,
      );
      fs.mkdirSync(sessionDir, { recursive: true });
      const sessionFile = path.join(sessionDir, "session-with-image.jsonl");
      fs.writeFileSync(
        sessionFile,
        [
          JSON.stringify({
            type: "session",
            id: "image-id",
            timestamp: "2025-01-03T00:00:00Z",
            cwd: tmpDir,
          }),
          JSON.stringify({
            type: "message",
            id: "msg-1",
            parentId: null,
            timestamp: new Date().toISOString(),
            message: {
              role: "user",
              content: [
                { type: "text", text: "Title from text block" },
                {
                  type: "image",
                  data: "a".repeat(90 * 1024),
                  mimeType: "image/png",
                },
              ],
              timestamp: Date.now(),
            },
          }),
        ].join("\n") + "\n",
      );
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);

      const command: RpcCommand = {
        id: "cmd-image",
        type: "list_sessions",
        workspacePath: tmpDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const responseCall = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "list_sessions" &&
          call.payload.id === "cmd-image",
      );

      expect(responseCall?.payload.success).toBe(true);
      expect(responseCall?.payload.data.sessions).toContainEqual(
        expect.objectContaining({
          id: "image-id",
          name: "Title from text block",
          path: sessionFile,
        }),
      );

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("filters active sessions out of workspace-scoped session lists", async () => {
      const workspaceA = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-workspace-a-"),
      );
      const workspaceB = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-workspace-b-"),
      );
      const liveSessionFile = path.join(workspaceA, "live-session.jsonl");
      fs.writeFileSync(
        liveSessionFile,
        [
          JSON.stringify({
            type: "session",
            id: "live-id",
            timestamp: "2025-01-02T00:00:00Z",
            cwd: workspaceA,
          }),
          JSON.stringify({
            type: "message",
            id: "live-msg-1",
            parentId: null,
            timestamp: new Date().toISOString(),
            message: {
              role: "user",
              content: "Live session",
              timestamp: Date.now(),
            },
          }),
        ].join("\n") + "\n",
      );

      const workspaceBSessionDir = path.join(
        process.env.PI_WEB_SESSIONS_ROOT!,
        `--${workspaceB.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`,
      );
      fs.mkdirSync(workspaceBSessionDir, { recursive: true });
      const workspaceBSessionFile = path.join(
        workspaceBSessionDir,
        "workspace-b-session.jsonl",
      );
      fs.writeFileSync(
        workspaceBSessionFile,
        [
          JSON.stringify({
            type: "session",
            id: "workspace-b-id",
            timestamp: "2025-01-03T00:00:00Z",
            cwd: workspaceB,
          }),
          JSON.stringify({
            type: "message",
            id: "workspace-b-msg-1",
            parentId: null,
            timestamp: new Date().toISOString(),
            message: {
              role: "user",
              content: "Workspace B session",
              timestamp: Date.now(),
            },
          }),
        ].join("\n") + "\n",
      );

      context.state.cwd = workspaceA;
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(liveSessionFile);
      (
        context.state.sessionManager.getHeader as ReturnType<typeof vi.fn>
      ).mockReturnValue({
        id: "live-id",
        timestamp: "2025-01-02T00:00:00Z",
        cwd: workspaceA,
      });
      (
        context.state.sessionManager.getCwd as ReturnType<typeof vi.fn>
      ).mockReturnValue(workspaceA);

      const command: RpcCommand = {
        id: "cmd-workspace-sessions",
        type: "list_sessions",
        workspacePath: workspaceB,
        includeActive: true,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(true);
      expect(response.payload.data.workspacePath).toBe(workspaceB);
      expect(response.payload.data.sessions).toEqual([
        expect.objectContaining({
          id: "workspace-b-id",
          path: workspaceBSessionFile,
          workspacePath: workspaceB,
        }),
      ]);
      expect(response.payload.data.sessions).not.toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: "live-id",
            workspacePath: workspaceA,
          }),
        ]),
      );

      fs.rmSync(workspaceA, { recursive: true, force: true });
      fs.rmSync(workspaceB, { recursive: true, force: true });
    });

    it("requires workspacePath for workspace-scoped session lists", async () => {
      const command: RpcCommand = {
        id: "cmd-workspace-missing-path",
        type: "list_sessions",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.command).toBe("list_sessions");
      expect(response.payload.success).toBe(false);
      expect(response.payload.error).toBe("workspacePath is required");
    });

    it("omits a pending new session from list_sessions until it is stored", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-pending-"));
      const sessionDir = path.join(
        process.env.PI_WEB_SESSIONS_ROOT!,
        `--${tmpDir.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`,
      );
      fs.mkdirSync(sessionDir, { recursive: true });
      const liveSessionFile = path.join(sessionDir, "live-session.jsonl");
      fs.writeFileSync(
        liveSessionFile,
        [
          JSON.stringify({
            type: "session",
            id: "live-id",
            timestamp: "2025-01-02T00:00:00Z",
            cwd: tmpDir,
          }),
          JSON.stringify({
            type: "message",
            id: "live-msg-1",
            parentId: null,
            timestamp: new Date().toISOString(),
            message: {
              role: "user",
              content: "Current session",
              timestamp: Date.now(),
            },
          }),
        ].join("\n") + "\n",
      );
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(liveSessionFile);
      vi.mocked(SessionManager.listAll).mockResolvedValue([
        {
          path: liveSessionFile,
          id: "live-id",
          cwd: tmpDir,
          created: new Date("2025-01-02T00:00:00Z"),
          modified: new Date("2025-01-02T00:00:00Z"),
          messageCount: 1,
          firstMessage: "Current session",
          allMessagesText: "Current session",
        },
      ]);

      const newSessionCommand: RpcCommand = {
        id: "cmd-new",
        type: "new_session",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: newSessionCommand }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      const listCommand: RpcCommand = {
        id: "cmd-list",
        type: "list_sessions",
        workspacePath: tmpDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: listCommand })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(true);
      expect(response.payload.data.sessions).toEqual([
        expect.objectContaining({
          id: "live-id",
          path: liveSessionFile,
        }),
      ]);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("keeps a pending new session switchable even when omitted from list_sessions", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-pending-"));
      const sessionDir = path.join(
        process.env.PI_WEB_SESSIONS_ROOT!,
        `--${tmpDir.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`,
      );
      fs.mkdirSync(sessionDir, { recursive: true });
      const liveSessionFile = path.join(sessionDir, "live-session.jsonl");
      fs.writeFileSync(
        liveSessionFile,
        [
          JSON.stringify({
            type: "session",
            id: "live-id",
            timestamp: "2025-01-02T00:00:00Z",
            cwd: tmpDir,
          }),
          JSON.stringify({
            type: "message",
            id: "live-msg-1",
            parentId: null,
            timestamp: new Date().toISOString(),
            message: {
              role: "user",
              content: "Current session",
              timestamp: Date.now(),
            },
          }),
        ].join("\n") + "\n",
      );
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(liveSessionFile);
      vi.mocked(SessionManager.listAll).mockResolvedValue([
        {
          path: liveSessionFile,
          id: "live-id",
          cwd: tmpDir,
          created: new Date("2025-01-02T00:00:00Z"),
          modified: new Date("2025-01-02T00:00:00Z"),
          messageCount: 1,
          firstMessage: "Current session",
          allMessagesText: "Current session",
        },
      ]);

      const newSessionCommand: RpcCommand = {
        id: "cmd-new",
        type: "new_session",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: newSessionCommand }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const newSessionResponse = [...sendCalls]
        .reverse()
        .find(
          call =>
            call.type === "response" &&
            call.payload.command === "new_session" &&
            call.payload.success,
        );
      const pendingSessionPath = newSessionResponse?.payload.data.sessionPath;

      expect(typeof pendingSessionPath).toBe("string");
      expect(
        newSessionResponse?.payload.data.treeEntries.find(
          (entry: { type: string }) => entry.type === "model_change",
        ),
      ).toBeTruthy();

      const switchToLiveCommand: RpcCommand = {
        id: "cmd-switch-live",
        type: "switch_session",
        sessionPath: liveSessionFile,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: switchToLiveCommand }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      const listCommand: RpcCommand = {
        id: "cmd-list",
        type: "list_sessions",
        workspacePath: tmpDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: listCommand })),
      );

      await new Promise(r => setTimeout(r, 10));

      const switchBackCommand: RpcCommand = {
        id: "cmd-switch-back",
        type: "switch_session",
        sessionPath: pendingSessionPath,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: switchBackCommand }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      const responses = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const listResponse = [...responses]
        .reverse()
        .find(
          call =>
            call.type === "response" &&
            call.payload.command === "list_sessions" &&
            call.payload.success,
        );
      const switchBackResponse = [...responses]
        .reverse()
        .find(
          call =>
            call.type === "response" &&
            call.payload.command === "switch_session" &&
            call.payload.id === "cmd-switch-back",
        );

      expect(listResponse?.payload.data.sessions).toEqual([
        expect.objectContaining({ path: liveSessionFile }),
      ]);
      expect(switchBackResponse?.payload.success).toBe(true);
      expect(switchBackResponse?.payload.data.sessionPath).toBe(
        pendingSessionPath,
      );

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("should handle list_sessions when no session file is available", async () => {
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(undefined);

      const command: RpcCommand = {
        id: "cmd-1",
        type: "list_sessions",
        workspacePath: "/test/project",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(true);
      expect(response.payload.data.sessions).toEqual([]);
    });

    it("lists registered workspaces even before the first session exists", async () => {
      const workspaceRoot = fs.mkdtempSync(
        path.join(os.tmpdir(), "piwebregisteredworkspace"),
      );
      const workspaceDir = path.join(workspaceRoot, "example");
      fs.mkdirSync(workspaceDir);
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(undefined);

      const registerCommand: RpcCommand = {
        id: "cmd-register",
        type: "register_workspace",
        workspacePath: workspaceDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: registerCommand }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      const listCommand: RpcCommand = {
        id: "cmd-list",
        type: "list_workspaces",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: listCommand })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const registerResponse = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "register_workspace" &&
          call.payload.id === "cmd-register",
      );
      const listResponse = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "list_workspaces" &&
          call.payload.id === "cmd-list",
      );

      expect(registerResponse?.payload.success).toBe(true);
      expect(registerResponse?.payload.data.cancelled).toBe(false);
      expect(registerResponse?.payload.data.workspacePath).toBe(workspaceDir);
      expect(listResponse?.payload.success).toBe(true);
      expect(listResponse?.payload.data.workspaces).toContainEqual({
        id: workspaceDir,
        name: path.basename(workspaceDir),
        path: workspaceDir,
      });
    });

    it("uses DANO_SESSIONS_ROOT before the Pi compatibility sessions root", async () => {
      const danoSessionsRoot = fs.mkdtempSync(
        path.join(os.tmpdir(), "dano-sessions-root-"),
      );
      const piSessionsRoot = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-compat-sessions-root-"),
      );
      process.env.DANO_SESSIONS_ROOT = danoSessionsRoot;
      process.env.PI_WEB_SESSIONS_ROOT = piSessionsRoot;

      const workspaceRoot = fs.mkdtempSync(
        path.join(os.tmpdir(), "dano-workspace-root-"),
      );
      const workspaceDir = path.join(workspaceRoot, "project");
      fs.mkdirSync(workspaceDir);

      const registerCommand: RpcCommand = {
        id: "cmd-register-dano-root",
        type: "register_workspace",
        workspacePath: workspaceDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: registerCommand }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      delete process.env.DANO_SESSIONS_ROOT;

      const sessionDirName = `--${workspaceDir
        .replace(/^[/\\]/, "")
        .replace(/[/\\:]/g, "-")}--`;
      expect(fs.existsSync(path.join(danoSessionsRoot, sessionDirName))).toBe(
        true,
      );
      expect(fs.existsSync(path.join(piSessionsRoot, sessionDirName))).toBe(
        false,
      );
    });

    it("creates the workspace directory when registering a missing workspace", async () => {
      const workspaceRoot = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-missing-workspace-"),
      );
      const workspaceDir = path.join(workspaceRoot, "created");

      const registerCommand: RpcCommand = {
        id: "cmd-register-created",
        type: "register_workspace",
        workspacePath: workspaceDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: registerCommand }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const registerResponse = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "register_workspace" &&
          call.payload.id === "cmd-register-created",
      );

      expect(registerResponse?.payload.success).toBe(true);
      expect(registerResponse?.payload.data.workspacePath).toBe(workspaceDir);
      expect(fs.statSync(workspaceDir).isDirectory()).toBe(true);
    });

    it("restores registered workspace paths when directory names contain hyphens", async () => {
      const workspaceRoot = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-registered-workspace-"),
      );
      const workspaceDir = path.join(workspaceRoot, "demo-project");
      fs.mkdirSync(workspaceDir);
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(undefined);

      const registerCommand: RpcCommand = {
        id: "cmd-register-hyphen",
        type: "register_workspace",
        workspacePath: workspaceDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: registerCommand }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      const listCommand: RpcCommand = {
        id: "cmd-list-hyphen",
        type: "list_workspaces",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: listCommand })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const listResponse = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "list_workspaces" &&
          call.payload.id === "cmd-list-hyphen",
      );

      expect(listResponse?.payload.success).toBe(true);
      expect(listResponse?.payload.data.workspaces).toContainEqual({
        id: workspaceDir,
        name: path.basename(workspaceDir),
        path: workspaceDir,
      });
    });

    it("normalizes trailing slashes when aggregating workspaces", async () => {
      const workspaceRoot = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-trailing-workspace-"),
      );
      const workspaceDir = path.join(workspaceRoot, "example");
      fs.mkdirSync(workspaceDir);
      const workspaceDirWithSlash = `${workspaceDir}${path.sep}`;

      const legacyRegisteredDir = path.join(
        process.env.PI_WEB_SESSIONS_ROOT!,
        `--${workspaceDirWithSlash.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`,
      );
      fs.mkdirSync(legacyRegisteredDir, { recursive: true });

      (
        context.state.sessionManager.getCwd as ReturnType<typeof vi.fn>
      ).mockReturnValue(workspaceDirWithSlash);
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(undefined);

      const listCommand: RpcCommand = {
        id: "cmd-list-normalized",
        type: "list_workspaces",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: listCommand })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const listResponse = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "list_workspaces" &&
          call.payload.id === "cmd-list-normalized",
      );

      expect(listResponse?.payload.success).toBe(true);
      expect(listResponse?.payload.data.workspaces).toEqual([
        expect.objectContaining({
          id: workspaceDir,
          name: path.basename(workspaceDir),
          path: workspaceDir,
        }),
      ]);
    });

    it("should handle list_workspace_entries command", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-workspace-test-"),
      );
      fs.mkdirSync(path.join(tmpDir, "src", "components"), {
        recursive: true,
      });
      fs.writeFileSync(path.join(tmpDir, ".gitignore"), "ignored.log\n");
      fs.writeFileSync(path.join(tmpDir, ".env"), "SECRET=1\n");
      fs.writeFileSync(path.join(tmpDir, "README.md"), "# test\n");
      fs.writeFileSync(path.join(tmpDir, "ignored.log"), "skip\n");
      fs.writeFileSync(path.join(tmpDir, "src", "index.ts"), "export {};\n");
      fs.writeFileSync(
        path.join(tmpDir, "src", "components", "ComposerBar.vue"),
        "<template />\n",
      );
      context.state.cwd = os.tmpdir();

      const command: RpcCommand = {
        id: "cmd-workspace",
        type: "list_workspace_entries",
        workspacePath: tmpDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.command).toBe("list_workspace_entries");
      expect(response.payload.success).toBe(true);
      expect(response.payload.data.entries).toEqual(
        expect.arrayContaining([
          { path: ".env", kind: "file" },
          { path: ".gitignore", kind: "file" },
          { path: "README.md", kind: "file" },
          { path: "src", kind: "directory" },
          { path: "src/components", kind: "directory" },
          { path: "src/index.ts", kind: "file" },
          { path: "src/components/ComposerBar.vue", kind: "file" },
        ]),
      );
      expect(response.payload.data.entries).not.toContainEqual({
        path: "ignored.log",
        kind: "file",
      });

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("includes files from symlinked directories in workspace entries", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-workspace-symlink-test-"),
      );
      const targetDir = path.join(tmpDir, "actual-src");
      const linkDir = path.join(tmpDir, "linked-src");
      fs.mkdirSync(path.join(targetDir, "nested"), { recursive: true });
      fs.writeFileSync(
        path.join(targetDir, "nested", "linked.ts"),
        "export const linked = true;\n",
      );
      fs.symlinkSync(
        targetDir,
        linkDir,
        process.platform === "win32" ? "junction" : "dir",
      );
      context.state.cwd = os.tmpdir();

      const command: RpcCommand = {
        id: "cmd-workspace-symlink",
        type: "list_workspace_entries",
        workspacePath: tmpDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.command).toBe("list_workspace_entries");
      expect(response.payload.success).toBe(true);
      expect(response.payload.data.entries).toEqual(
        expect.arrayContaining([
          { path: "linked-src", kind: "directory" },
          { path: "linked-src/nested", kind: "directory" },
          { path: "linked-src/nested/linked.ts", kind: "file" },
        ]),
      );

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("refreshes workspace entry cache when forced or the workspace changes", async () => {
      const workspaceA = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-workspace-a-"),
      );
      const workspaceB = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-workspace-b-"),
      );
      fs.writeFileSync(path.join(workspaceA, "alpha.txt"), "alpha\n");
      fs.writeFileSync(path.join(workspaceB, "beta.txt"), "beta\n");

      const listEntries = async (
        id: string,
        workspacePath: string,
        options?: { force?: boolean },
      ): Promise<{ payload: { data: { entries: RpcWorkspaceEntry[] } } }> => {
        const callCountBefore = (ws.send as ReturnType<typeof vi.fn>).mock.calls
          .length;
        const command: RpcCommand = {
          id,
          type: "list_workspace_entries",
          workspacePath,
          ...(options?.force ? { force: true } : {}),
        };
        (
          ws as unknown as { trigger: (event: string, data: Buffer) => void }
        ).trigger(
          "message",
          Buffer.from(JSON.stringify({ type: "command", payload: command })),
        );

        await new Promise(r => setTimeout(r, 10));

        const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
        return JSON.parse(sendCalls[callCountBefore][0] as string) as {
          payload: { data: { entries: RpcWorkspaceEntry[] } };
        };
      };

      context.state.cwd = os.tmpdir();
      const firstResponse = await listEntries("cmd-workspace-a", workspaceA);
      expect(firstResponse.payload.data.entries).toEqual(
        expect.arrayContaining([{ path: "alpha.txt", kind: "file" }]),
      );

      fs.writeFileSync(path.join(workspaceA, "fresh.txt"), "fresh\n");
      const forcedResponse = await listEntries(
        "cmd-workspace-force",
        workspaceA,
        {
          force: true,
        },
      );
      expect(forcedResponse.payload.data.entries).toEqual(
        expect.arrayContaining([{ path: "fresh.txt", kind: "file" }]),
      );

      const secondResponse = await listEntries("cmd-workspace-b", workspaceB);
      expect(secondResponse.payload.data.entries).toEqual(
        expect.arrayContaining([{ path: "beta.txt", kind: "file" }]),
      );
      expect(secondResponse.payload.data.entries).not.toEqual(
        expect.arrayContaining([{ path: "alpha.txt", kind: "file" }]),
      );

      fs.rmSync(workspaceA, { recursive: true, force: true });
      fs.rmSync(workspaceB, { recursive: true, force: true });
    });

    it("should handle read_workspace_file command", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-read-file-test-"),
      );
      const filePath = path.join(tmpDir, "src", "App.vue");
      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      fs.writeFileSync(filePath, "<template>\n  <main />\n</template>\n");
      context.state.cwd = os.tmpdir();

      const command: RpcCommand = {
        id: "cmd-read-file",
        type: "read_workspace_file",
        path: "src/App.vue",
        workspacePath: tmpDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.command).toBe("read_workspace_file");
      expect(response.payload.success).toBe(true);
      expect(response.payload.data).toMatchObject({
        path: "src/App.vue",
        absolutePath: fs.realpathSync(filePath),
        truncated: false,
        totalBytes: fs.statSync(filePath).size,
        lineCount: 4,
      });
      expect(response.payload.data.content).toContain("<main />");

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("should reject read_workspace_file outside the workspace", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-read-file-guard-test-"),
      );
      const outsideDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-outside-file-test-"),
      );
      const outsideFile = path.join(outsideDir, "outside.txt");
      fs.writeFileSync(outsideFile, "outside\n");
      context.state.cwd = os.tmpdir();

      const command: RpcCommand = {
        id: "cmd-read-file-outside",
        type: "read_workspace_file",
        path: outsideFile,
        workspacePath: tmpDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.command).toBe("read_workspace_file");
      expect(response.payload.success).toBe(false);
      expect(response.payload.error).toContain("inside the current workspace");

      fs.rmSync(tmpDir, { recursive: true, force: true });
      fs.rmSync(outsideDir, { recursive: true, force: true });
    });

    it("should handle list_tree_entries command", async () => {
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockReturnValue([
        {
          id: "entry-1",
          role: "user",
          type: "message",
          timestamp: "2025-01-01T00:00:00Z",
        },
        {
          id: "entry-2",
          role: "assistant",
          type: "message",
          timestamp: "2025-01-01T00:01:00Z",
        },
      ]);

      const command: RpcCommand = { id: "cmd-1", type: "list_tree_entries" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.type).toBe("response");
      expect(response.payload.command).toBe("list_tree_entries");
      expect(response.payload.success).toBe(true);
      expect(response.payload.data.entries).toHaveLength(2);
      expect(response.payload.data.entries[0]).toMatchObject({
        id: "entry-1",
        label: "user",
        type: "message",
        timestamp: "2025-01-01T00:00:00Z",
        parentId: null,
        depth: 0,
        trackColumns: [],
        isActive: false,
        isOnActivePath: true,
        role: "user",
        previewText: "user",
        isSettingsEntry: false,
        isLabeled: false,
        isToolOnlyAssistant: false,
      });
      expect(response.payload.data.entries[0].searchText).toContain("message");
    });

    it("should load list_tree_entries from the session file when available", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-tree-test-"),
      );
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendModelChange("openai", "gpt-4.1");
      sessionManager.appendThinkingLevelChange("high");
      sessionManager.appendMessage({
        role: "user",
        content: "Hello",
        timestamp: Date.now(),
      } as any);
      sessionManager.appendMessage({
        role: "assistant",
        content: [{ type: "text", text: "Hi" }],
        timestamp: Date.now(),
      } as any);
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);

      const command: RpcCommand = { id: "cmd-1", type: "list_tree_entries" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(true);
      expect(response.payload.data.entries).toHaveLength(4);
      expect(
        response.payload.data.entries.map(
          (entry: {
            label: string;
            role: string;
            isSettingsEntry: boolean;
          }) => ({
            label: entry.label,
            role: entry.role,
            isSettingsEntry: entry.isSettingsEntry,
          }),
        ),
      ).toEqual([
        {
          label: "[model: gpt-4.1]",
          role: "meta",
          isSettingsEntry: true,
        },
        {
          label: "[thinking: high]",
          role: "meta",
          isSettingsEntry: true,
        },
        {
          label: "user: Hello",
          role: "user",
          isSettingsEntry: false,
        },
        {
          label: "assistant: Hi",
          role: "assistant",
          isSettingsEntry: false,
        },
      ]);
      expect(response.payload.data.entries[3]).toMatchObject({
        label: "assistant: Hi",
        previewText: "Hi",
        depth: 0,
        isActive: true,
      });

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("should indent only after an actual branch point", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-branch-depth-test-"),
      );
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: "Start",
        timestamp: Date.now(),
      } as any);
      sessionManager.appendMessage({
        role: "assistant",
        content: [{ type: "text", text: "Choose a path" }],
        timestamp: Date.now(),
      } as any);
      const branchPoint = sessionManager.getLeafId();
      sessionManager.appendMessage({
        role: "user",
        content: "Path A",
        timestamp: Date.now(),
      } as any);
      if (!branchPoint) {
        throw new Error("branch point missing");
      }
      sessionManager.branch(branchPoint);
      sessionManager.appendMessage({
        role: "user",
        content: "Path B",
        timestamp: Date.now(),
      } as any);
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);

      const command: RpcCommand = { id: "cmd-1", type: "list_tree_entries" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(true);
      expect(
        response.payload.data.entries.map(
          (entry: { label: string; depth: number }) => ({
            label: entry.label,
            depth: entry.depth,
          }),
        ),
      ).toEqual([
        { label: "user: Start", depth: 0 },
        { label: "assistant: Choose a path", depth: 0 },
        { label: "user: Path B", depth: 1 },
        { label: "user: Path A", depth: 1 },
      ]);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("should handle list_tree_entries with empty branch", async () => {
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockReturnValue([]);

      const command: RpcCommand = { id: "cmd-1", type: "list_tree_entries" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(true);
      expect(response.payload.data.entries).toHaveLength(0);
    });

    it("should filter entries without id in list_tree_entries", async () => {
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockReturnValue([
        { id: "entry-1", role: "user" },
        { role: "orphan", type: "message" }, // no id
      ]);

      const command: RpcCommand = { id: "cmd-1", type: "list_tree_entries" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(true);
      expect(response.payload.data.entries).toHaveLength(1);
      expect(response.payload.data.entries[0].id).toBe("entry-1");
    });

    it("should return empty sessions when scanning fails", async () => {
      // Force an error in session scanning by making getSessionFile throw
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockImplementation(() => {
        throw new Error("session file unavailable");
      });

      const command: RpcCommand = {
        id: "cmd-1",
        type: "list_sessions",
        workspacePath: "/test/project",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(true);
      expect(response.payload.data.sessions).toEqual([]);
    });

    it("should return empty entries when getBranch throws", async () => {
      (
        context.state.sessionManager.getBranch as ReturnType<typeof vi.fn>
      ).mockImplementation(() => {
        throw new Error("Branch error");
      });

      const command: RpcCommand = { id: "cmd-1", type: "list_tree_entries" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = sendCalls[sendCalls.length - 1][0] as string;
      const response = JSON.parse(lastCall);

      expect(response.payload.success).toBe(true);
      expect(response.payload.data.entries).toEqual([]);
    });
  });

  describe("disposal", () => {
    it("should resolve pending UI requests on dispose", async () => {
      const uiContext = adapter.createExtensionUIContext();

      // Start a request
      const selectPromise = uiContext.select("Choose", ["a", "b"]);
      await new Promise(r => setTimeout(r, 10));

      // Dispose before response
      adapter.dispose();

      // Should resolve with default value
      const result = await selectPromise;
      expect(result).toBeUndefined();
    });

    it("should emit client_disconnect on dispose", () => {
      adapter.dispose();

      expect(emitEvent).toHaveBeenCalledWith({
        type: "client_disconnect",
        client,
        reason: "adapter_disposed",
      });
    });

    it("should not send responses after dispose", async () => {
      adapter.dispose();
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: { type: "get_state" } }),
        ),
      );
      await new Promise(resolve => setTimeout(resolve, 10));

      expect(ws.send).not.toHaveBeenCalled();
    });
  });

  describe("command correlation", () => {
    it("should use provided correlation ID", async () => {
      const command: RpcCommand = {
        id: "my-correlation-id",
        type: "get_state",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      expect(emitEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          correlationId: "my-correlation-id",
        }),
      );
    });

    it("should generate correlation ID if not provided", async () => {
      const command: RpcCommand = { type: "get_state" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const call = (emitEvent as ReturnType<typeof vi.fn>).mock.calls.find(
        (call: unknown[]) =>
          (call[0] as { type: string }).type === "command_received",
      );
      expect(call).toBeDefined();

      const event = call?.[0] as { correlationId: string };
      expect(typeof event.correlationId).toBe("string");
      expect(event.correlationId).toHaveLength(36); // UUID length
    });
  });

  describe("session commands", () => {
    it("should handle set_session_name with valid name", async () => {
      const command: RpcCommand = {
        id: "cmd-1",
        type: "set_session_name",
        name: "New Session Name",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      expect(context.actions.setSessionName).toHaveBeenCalledWith(
        "New Session Name",
      );

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = JSON.parse(sendCalls[sendCalls.length - 1][0] as string);
      expect(lastCall.payload.success).toBe(true);
    });

    it("should reject empty session name", async () => {
      const command: RpcCommand = {
        id: "cmd-1",
        type: "set_session_name",
        name: "   ",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = JSON.parse(sendCalls[sendCalls.length - 1][0] as string);
      expect(lastCall.payload.success).toBe(false);
      expect(lastCall.payload.error).toContain("cannot be empty");
    });

    it("should handle new_session command", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-test-"));
      const sm = SessionManager.create(tmpDir, tmpDir);
      const existingFile = sm.getSessionFile()!;
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(existingFile);

      const command: RpcCommand = { id: "cmd-1", type: "new_session" };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      // ctx.newSession should NOT be called (bridge creates session locally)
      // createAgentSession should NOT be called eagerly
      expect(createAgentSessionMock).not.toHaveBeenCalled();

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const responseCall = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "new_session" &&
          call.payload.success,
      );
      expect(responseCall?.payload.data.cancelled).toBe(false);
      expect(responseCall?.payload.data.sessionId).toBeTruthy();
      expect(responseCall?.payload.data.sessionName).toBe("New session");
      expect(responseCall?.payload.data.model).toMatchObject({
        provider: "openai",
        id: "gpt-4",
      });
      expect(responseCall?.payload.data.thinkingLevel).toBe("medium");
      expect(responseCall?.payload.data.transcript.messages).toEqual([]);
      expect(responseCall?.payload.data.transcript.hasOlder).toBe(false);
      expect(
        responseCall?.payload.data.treeEntries.find(
          (entry: { type: string }) => entry.type === "model_change",
        ),
      ).toBeTruthy();

      const statsEvent = sendCalls.find(
        call =>
          call.type === "event" &&
          call.payload.type === "session_stats" &&
          call.payload.sessionPath === responseCall?.payload.data.sessionPath,
      );
      expect(statsEvent?.payload).toMatchObject({
        type: "session_stats",
        sessionPath: responseCall?.payload.data.sessionPath,
        stats: {
          tokens: null,
          contextWindow: 0,
          percent: null,
          inputTokens: 0,
          outputTokens: 0,
          cacheReadTokens: 0,
          cacheWriteTokens: 0,
          cost: 0,
        },
      });

      // Clean up temp dir
      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("uses Dano defaults without forcing an empty session jsonl", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-defaults-"));
      const sm = SessionManager.create(tmpDir, tmpDir);
      const existingFile = sm.getSessionFile()!;
      const xiaomiModel = {
        id: "mimo-v2.5",
        name: "MiMo V2.5",
        api: "openai-responses",
        provider: "xiaomi-token-plan-cn",
        reasoning: true,
        contextWindow: 128000,
        maxTokens: 8192,
      };
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(existingFile);
      (context.state.getAvailableModels as ReturnType<typeof vi.fn>).mockReturnValue(
        [xiaomiModel],
      );
      (context.state.getDefaultModels as ReturnType<typeof vi.fn>).mockReturnValue([
        {
          provider: "xiaomi-token-plan-cn",
          modelId: "mimo-v2.5",
        },
        {
          provider: "openai",
          modelId: "gpt-4",
        },
      ]);
      (
        context.state.getDefaultThinkingLevel as ReturnType<typeof vi.fn>
      ).mockReturnValue("medium");

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: { id: "cmd-dano-new", type: "new_session" },
          }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      let sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const newSessionResponse = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "new_session" &&
          call.payload.id === "cmd-dano-new",
      );
      const sessionPath = newSessionResponse?.payload.data.sessionPath;
      expect(newSessionResponse?.payload.success).toBe(true);
      expect(newSessionResponse?.payload.data.model).toMatchObject({
        provider: "xiaomi-token-plan-cn",
        id: "mimo-v2.5",
      });
      expect(newSessionResponse?.payload.data.thinkingLevel).toBe("medium");
      expect(typeof sessionPath).toBe("string");
      expect(fs.existsSync(sessionPath)).toBe(false);

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: { id: "cmd-dano-state", type: "get_state" },
          }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(call =>
        JSON.parse(call[0] as string),
      );
      const stateResponse = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "get_state" &&
          call.payload.id === "cmd-dano-state",
      );
      expect(stateResponse?.payload.success).toBe(true);
      expect(stateResponse?.payload.data.model).toMatchObject({
        provider: "xiaomi-token-plan-cn",
        id: "mimo-v2.5",
      });
      expect(stateResponse?.payload.data.thinkingLevel).toBe("medium");

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("restores get_state model from an old session model_change", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-old-model-"));
      const sessionFile = path.join(tmpDir, "old-session.jsonl");
      fs.writeFileSync(
        sessionFile,
        [
          JSON.stringify({
            type: "session",
            version: 3,
            id: "old-model-session",
            timestamp: "2025-01-02T00:00:00Z",
            cwd: tmpDir,
          }),
          JSON.stringify({
            type: "model_change",
            id: "old-model-entry",
            parentId: null,
            timestamp: "2025-01-02T00:00:01Z",
            provider: "openai",
            modelId: "gpt-4.1",
          }),
        ].join("\n") + "\n",
      );

      const switchCommand: RpcCommand = {
        id: "cmd-switch-old-model",
        type: "switch_session",
        sessionPath: sessionFile,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: switchCommand })),
      );

      await new Promise(r => setTimeout(r, 10));

      const stateCommand: RpcCommand = {
        id: "cmd-old-model-state",
        type: "get_state",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: stateCommand })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const response = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "get_state" &&
          call.payload.id === "cmd-old-model-state",
      );

      expect(response?.payload.success).toBe(true);
      expect(response?.payload.data.model).toEqual({
        provider: "openai",
        id: "gpt-4.1",
      });

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("creates the workspace directory when starting a new session", async () => {
      const workspaceRoot = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-new-session-workspace-"),
      );
      const workspaceDir = path.join(workspaceRoot, "created");

      const command: RpcCommand = {
        id: "cmd-new-created",
        type: "new_session",
        workspacePath: workspaceDir,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const responseCall = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "new_session" &&
          call.payload.id === "cmd-new-created",
      );

      expect(responseCall?.payload.success).toBe(true);
      expect(responseCall?.payload.data.workspacePath).toBe(workspaceDir);
      expect(fs.statSync(workspaceDir).isDirectory()).toBe(true);
    });

    it("should handle fork command", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-test-"));
      const sm = SessionManager.create(tmpDir, tmpDir);
      sm.appendModelChange("openai", "gpt-4.1");
      sm.appendThinkingLevelChange("high");
      sm.appendMessage({
        role: "user",
        content: "Hello",
        timestamp: Date.now(),
      } as any);
      sm.appendMessage({
        role: "assistant",
        content: [{ type: "text", text: "Hi" }],
        timestamp: Date.now(),
      } as any);
      const leafId = sm.getLeafId() as string;
      const existingFile = sm.getSessionFile() as string;
      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(existingFile);

      const command: RpcCommand = {
        id: "cmd-1",
        type: "fork",
        entryId: leafId,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      // ctx.fork should NOT be called (bridge creates fork locally)

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = JSON.parse(sendCalls[sendCalls.length - 1][0] as string);
      expect(lastCall.payload.success).toBe(true);
      expect(lastCall.payload.data.cancelled).toBe(false);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("should open a detached session before navigate_tree on the live session", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-nav-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: "Revise me",
        timestamp: Date.now(),
      } as any);
      const entryId = sessionManager.getLeafId();
      const sessionFile = sessionManager.getSessionFile();
      if (!entryId || !sessionFile) {
        throw new Error("session fixture missing");
      }

      const header = {
        type: "session",
        version: 3,
        id: sessionManager.getSessionId(),
        timestamp: new Date().toISOString(),
        cwd: tmpDir,
      };
      fs.writeFileSync(
        sessionFile,
        [
          JSON.stringify(header),
          ...sessionManager.getEntries().map(entry => JSON.stringify(entry)),
        ].join("\n"),
      );

      (
        context.state.sessionManager.getSessionFile as ReturnType<typeof vi.fn>
      ).mockReturnValue(sessionFile);

      const navigateTreeSpy = vi.fn().mockImplementation(async targetId => {
        const target = sessionManager.getEntry(targetId as string);
        if (target?.parentId) {
          sessionManager.branch(target.parentId);
        } else {
          sessionManager.resetLeaf();
        }
        return { cancelled: false, editorText: "Revise me" };
      });
      createAgentSessionMock.mockResolvedValue({
        session: {
          sessionFile,
          sessionId: sessionManager.getSessionId(),
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: vi.fn().mockReturnValue(() => {}),
          navigateTree: navigateTreeSpy,
          sessionManager,
        },
      });

      const command: RpcCommand = {
        id: "cmd-1",
        type: "navigate_tree",
        entryId,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 20));

      expect(createAgentSessionMock).toHaveBeenCalledTimes(1);
      expect(navigateTreeSpy).toHaveBeenCalledWith(entryId, {
        summarize: undefined,
        customInstructions: undefined,
        replaceInstructions: undefined,
        label: undefined,
      });

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const snapshotCall = [...sendCalls]
        .reverse()
        .find(
          call =>
            call.type === "event" &&
            call.payload.type === "transcript_snapshot" &&
            call.payload.sessionPath === sessionFile,
        );
      expect(snapshotCall?.payload.messages).toEqual([]);

      const lastCall = sendCalls[sendCalls.length - 1];
      expect(lastCall.payload.success).toBe(true);
      expect(lastCall.payload.data.cancelled).toBe(false);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("should select the requested user tree entry exactly", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-select-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: "First prompt",
        timestamp: Date.now(),
      } as any);
      sessionManager.appendMessage({
        role: "assistant",
        content: [{ type: "text", text: "First reply" }],
        timestamp: Date.now(),
      } as any);
      sessionManager.appendMessage({
        role: "user",
        content: "Second prompt",
        timestamp: Date.now(),
      } as any);
      sessionManager.appendMessage({
        role: "assistant",
        content: [{ type: "text", text: "Second reply" }],
        timestamp: Date.now(),
      } as any);

      const targetEntryId = String(
        (sessionManager.getEntries()[0] as { id: string }).id,
      );
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }

      const switchCommand: RpcCommand = {
        id: "switch-1",
        type: "switch_session",
        sessionPath: sessionFile,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: switchCommand }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      createAgentSessionMock.mockResolvedValue({
        session: {
          sessionFile,
          sessionId: sessionManager.getSessionId(),
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: vi.fn().mockReturnValue(() => {}),
          sessionManager,
        },
      });

      const command: RpcCommand = {
        id: "cmd-select-user",
        type: "select_tree_entry",
        entryId: targetEntryId,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 20));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const responseCall = [...sendCalls]
        .reverse()
        .find(
          call =>
            call.type === "response" &&
            call.payload.command === "select_tree_entry" &&
            call.payload.success,
        );

      expect(responseCall?.payload.data.transcript.messages).toHaveLength(1);
      expect(responseCall?.payload.data.transcript.messages[0]).toMatchObject({
        id: targetEntryId,
        role: "user",
        content: "First prompt",
      });
      expect(
        responseCall?.payload.data.treeEntries.find(
          (entry: { id: string }) => entry.id === targetEntryId,
        ),
      ).toMatchObject({ isActive: true, isOnActivePath: true });

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("should trim later tool calls when selecting a tool tree entry", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-select-tool-"),
      );
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: "Inspect the files",
        timestamp: Date.now(),
      } as any);
      sessionManager.appendMessage({
        role: "assistant",
        content: [
          { type: "thinking", thinking: "Inspecting both files" },
          {
            type: "toolCall",
            id: "tool-1",
            name: "read",
            arguments: '{"path":"a.txt"}',
          },
          {
            type: "toolCall",
            id: "tool-2",
            name: "read",
            arguments: '{"path":"b.txt"}',
          },
        ],
        timestamp: Date.now(),
      } as any);
      sessionManager.appendMessage({
        role: "toolResult",
        toolCallId: "tool-1",
        toolName: "read",
        content: [{ type: "text", text: "a.txt contents" }],
        timestamp: Date.now(),
      } as any);
      sessionManager.appendMessage({
        role: "toolResult",
        toolCallId: "tool-2",
        toolName: "read",
        content: [{ type: "text", text: "b.txt contents" }],
        timestamp: Date.now(),
      } as any);

      const targetEntryId = String(
        (sessionManager.getEntries()[2] as { id: string }).id,
      );
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }

      const switchCommand: RpcCommand = {
        id: "switch-2",
        type: "switch_session",
        sessionPath: sessionFile,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({ type: "command", payload: switchCommand }),
        ),
      );

      await new Promise(r => setTimeout(r, 10));

      createAgentSessionMock.mockResolvedValue({
        session: {
          sessionFile,
          sessionId: sessionManager.getSessionId(),
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: vi.fn().mockReturnValue(() => {}),
          sessionManager,
        },
      });

      const command: RpcCommand = {
        id: "cmd-select-tool",
        type: "select_tree_entry",
        entryId: targetEntryId,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 20));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const responseCall = [...sendCalls]
        .reverse()
        .find(
          call =>
            call.type === "response" &&
            call.payload.command === "select_tree_entry" &&
            call.payload.success,
        );

      const messages = responseCall?.payload.data.transcript.messages as Array<{
        id?: string;
        role: string;
        toolCallId?: string;
        content?: unknown;
      }>;
      const assistantMessage = messages.find(
        message => message.role === "assistant",
      );

      expect(messages).toHaveLength(3);
      expect(assistantMessage?.content).toEqual([
        { type: "thinking", thinking: "Inspecting both files" },
        {
          type: "toolCall",
          id: "tool-1",
          name: "read",
          arguments: '{"path":"a.txt"}',
        },
      ]);
      expect(JSON.stringify(assistantMessage?.content)).not.toContain("tool-2");
      expect(messages.at(-1)).toMatchObject({
        id: targetEntryId,
        role: "toolResult",
        toolCallId: "tool-1",
      });
      expect(
        responseCall?.payload.data.treeEntries.find(
          (entry: { id: string }) => entry.id === targetEntryId,
        ),
      ).toMatchObject({ isActive: true, isOnActivePath: true });

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("should handle switch_session command", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-test-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendModelChange("openai", "gpt-4.1");
      sessionManager.appendThinkingLevelChange("high");
      sessionManager.appendMessage({
        role: "user",
        content: "Hello",
        timestamp: Date.now(),
      } as any);
      sessionManager.appendMessage({
        role: "assistant",
        content: [{ type: "text", text: "Hi" }],
        timestamp: Date.now(),
      } as any);
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }

      const command: RpcCommand = {
        id: "cmd-1",
        type: "switch_session",
        sessionPath: sessionFile,
        limit: 1,
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const responseCall = sendCalls.find(
        call =>
          call.type === "response" &&
          call.payload.command === "switch_session" &&
          call.payload.success,
      );
      expect(responseCall?.payload.data.transcript.messages).toHaveLength(1);
      expect(responseCall?.payload.data.transcript.messages[0]).toMatchObject({
        role: "assistant",
        content: [{ type: "text", text: "Hi" }],
      });
      expect(responseCall?.payload.data.transcript.hasOlder).toBe(true);
      expect(responseCall?.payload.data.sessionId).toBe(
        sessionManager.getSessionId(),
      );
      expect(responseCall?.payload.data.sessionName).toBe("Hello");
      expect(responseCall?.payload.data.treeEntries).toHaveLength(4);
      expect(
        responseCall?.payload.data.treeEntries.map(
          (entry: {
            label: string;
            role: string;
            isSettingsEntry: boolean;
          }) => ({
            label: entry.label,
            role: entry.role,
            isSettingsEntry: entry.isSettingsEntry,
          }),
        ),
      ).toEqual([
        {
          label: "[model: gpt-4.1]",
          role: "meta",
          isSettingsEntry: true,
        },
        {
          label: "[thinking: high]",
          role: "meta",
          isSettingsEntry: true,
        },
        {
          label: "user: Hello",
          role: "user",
          isSettingsEntry: false,
        },
        {
          label: "assistant: Hi",
          role: "assistant",
          isSettingsEntry: false,
        },
      ]);
      expect(responseCall?.payload.data.treeEntries[3]).toMatchObject({
        label: "assistant: Hi",
        type: "message",
        previewText: "Hi",
        depth: 0,
        isActive: true,
      });

      const statsEvent = sendCalls.find(
        call =>
          call.type === "event" &&
          call.payload.type === "session_stats" &&
          call.payload.sessionPath === sessionFile,
      );
      expect(statsEvent?.payload).toMatchObject({
        type: "session_stats",
        sessionPath: sessionFile,
        stats: {
          messageCount: 4,
          outputTokens: 0,
        },
      });

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("restores a presented question card when switching back after reconnect", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "dano-question-reconnect-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "assistant",
        content: [
          {
            type: "toolCall",
            id: "reconnected-question",
            name: "ask_user_question",
            arguments: {
              question: "请假原因？",
              default: "个人事务",
            },
          },
        ],
        timestamp: Date.now(),
      } as any);
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) throw new Error("session file was not created");

      const pending = askUserQuestionCoordinator.wait(
        "reconnected-question",
        { question: "请假原因？", default: "个人事务" },
        undefined,
      );
      askUserQuestionCoordinator.present("reconnected-question");

      ws.trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "switch-reconnected-question",
              type: "switch_session",
              sessionPath: sessionFile,
            },
          }),
        ),
      );
      await new Promise(resolve => setTimeout(resolve, 10));

      const response = (ws.send as ReturnType<typeof vi.fn>).mock.calls
        .map(([message]) => JSON.parse(message as string))
        .find(message => message.payload?.id === "switch-reconnected-question");
      expect(response.payload.data.transcript.messages[0].content[0]).toMatchObject({
        id: "reconnected-question",
        questionRequest: {
          batch: false,
          kind: "text",
          question: "请假原因？",
          default: "个人事务",
        },
        questionState: "presented",
      });

      askUserQuestionCoordinator.answer("reconnected-question", {
        cancelled: true,
      });
      await pending;
      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("should return error for switch_session with non-existent file", async () => {
      const command: RpcCommand = {
        id: "cmd-1",
        type: "switch_session",
        sessionPath: "/non/existent/path.json",
      };
      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(JSON.stringify({ type: "command", payload: command })),
      );

      await new Promise(r => setTimeout(r, 10));

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls;
      const lastCall = JSON.parse(sendCalls[sendCalls.length - 1][0] as string);
      expect(lastCall.payload.success).toBe(false);
      expect(lastCall.payload.error).toContain("not found");
    });
  });

  describe("responsibility boundaries", () => {
    it("keeps the active detached session alive when switching to another stored session", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-runtime-"));
      const firstManager = SessionManager.create(tmpDir, tmpDir);
      firstManager.appendMessage({
        role: "user",
        content: [{ type: "text", text: "First session" }],
        timestamp: Date.now(),
      });
      const secondManager = SessionManager.create(tmpDir, tmpDir);
      secondManager.appendMessage({
        role: "user",
        content: [{ type: "text", text: "Second session" }],
        timestamp: Date.now(),
      });

      const firstSessionFile = firstManager.getSessionFile();
      const secondSessionFile = secondManager.getSessionFile();
      if (!firstSessionFile || !secondSessionFile) {
        throw new Error("expected persisted session files");
      }

      const persistSession = (
        sessionManager: SessionManager,
        sessionFile: string,
      ) => {
        const header = {
          type: "session",
          version: 3,
          id: sessionManager.getSessionId(),
          timestamp: new Date().toISOString(),
          cwd: tmpDir,
        };
        fs.writeFileSync(
          sessionFile,
          [
            JSON.stringify(header),
            ...sessionManager.getEntries().map(entry => JSON.stringify(entry)),
          ].join("\n"),
        );
      };
      persistSession(firstManager, firstSessionFile);
      persistSession(secondManager, secondSessionFile);

      const firstPromptSpy = vi.fn().mockResolvedValue(undefined);
      const firstDisposeSpy = vi.fn();
      const firstUnsubscribeSpy = vi.fn();
      const firstSubscribeSpy = vi.fn().mockReturnValue(firstUnsubscribeSpy);
      createAgentSessionMock.mockResolvedValueOnce({
        session: {
          sessionFile: firstSessionFile,
          sessionId: firstManager.getSessionId(),
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: firstSubscribeSpy,
          prompt: firstPromptSpy,
          dispose: firstDisposeSpy,
          sessionManager: firstManager,
        },
      });

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "switch-first",
              type: "switch_session",
              sessionPath: firstSessionFile,
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 10));

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "prompt-first",
              type: "prompt",
              message: "Activate first detached session",
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 20));

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "switch-second",
              type: "switch_session",
              sessionPath: secondSessionFile,
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 10));

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "switch-back-first",
              type: "switch_session",
              sessionPath: firstSessionFile,
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 10));

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "prompt-first-again",
              type: "prompt",
              message: "Resume first detached session",
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 20));

      expect(firstSubscribeSpy).toHaveBeenCalledTimes(1);
      expect(firstUnsubscribeSpy).not.toHaveBeenCalled();
      expect(firstDisposeSpy).not.toHaveBeenCalled();
      expect(createAgentSessionMock).toHaveBeenCalledTimes(1);
      expect(firstPromptSpy).toHaveBeenNthCalledWith(
        1,
        "Activate first detached session",
        {
          source: "rpc",
        },
      );
      expect(firstPromptSpy).toHaveBeenNthCalledWith(
        2,
        "Resume first detached session",
        {
          source: "rpc",
        },
      );

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("dequeues queued follow-up messages from the detached session", async () => {
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-dequeue-"));
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: [{ type: "text", text: "Detached session" }],
        timestamp: Date.now(),
      });
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }

      fs.writeFileSync(
        sessionFile,
        [
          JSON.stringify({
            type: "session",
            version: 3,
            id: sessionManager.getSessionId(),
            timestamp: new Date().toISOString(),
            cwd: tmpDir,
          }),
          ...sessionManager.getEntries().map(entry => JSON.stringify(entry)),
        ].join("\n"),
      );

      const listeners: Array<(event: object) => void> = [];
      const queuedMessages = [
        {
          role: "user",
          content: [{ type: "text", text: "Queued first" }],
          timestamp: 1,
        },
        {
          role: "user",
          content: [
            { type: "text", text: "Queued second" },
            {
              type: "image",
              data: "ZmFrZS1pbWFnZQ==",
              mimeType: "image/png",
            },
          ],
          timestamp: 2,
        },
      ];
      const session = {
        sessionFile,
        sessionId: sessionManager.getSessionId(),
        isStreaming: true,
        bindExtensions: vi.fn().mockResolvedValue(undefined),
        subscribe: vi
          .fn()
          .mockImplementation((listener: (event: object) => void) => {
            listeners.push(listener);
            return () => {};
          }),
        prompt: vi.fn().mockResolvedValue(undefined),
        sessionManager,
        agent: {
          steeringQueue: { messages: [] },
          followUpQueue: { messages: [...queuedMessages] },
        },
        _followUpMessages: ["Queued first", "Queued second"],
        _emitQueueUpdate: vi.fn(),
      } as any;
      session._emitQueueUpdate.mockImplementation(() => {
        const followUp = [...session._followUpMessages];
        for (const listener of listeners) {
          listener({ type: "queue_update", steering: [], followUp });
        }
      });

      createAgentSessionMock.mockResolvedValueOnce({ session });

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "switch-dequeue",
              type: "switch_session",
              sessionPath: sessionFile,
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 10));

      const switchResponse = (ws.send as ReturnType<typeof vi.fn>).mock.calls
        .map(call => JSON.parse(call[0] as string))
        .find(
          call =>
            call.type === "response" &&
            call.payload?.command === "switch_session",
        );
      expect(switchResponse?.payload?.success).toBe(true);
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "dequeue-follow-up",
              type: "dequeue_follow_up_message",
              index: 1,
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 10));

      const sentMessages = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const queueUpdate = sentMessages.find(
        call => call.type === "event" && call.payload?.type === "queue_update",
      );
      const response = sentMessages.find(
        call =>
          call.type === "response" &&
          call.payload?.command === "dequeue_follow_up_message",
      );

      expect(queueUpdate?.payload.followUp).toEqual([
        {
          text: "Queued first",
          images: [],
          timestamp: 1,
          queueType: "followUp",
        },
      ]);
      expect(response?.payload).toMatchObject({
        success: true,
        data: {
          removed: {
            text: "Queued second",
            images: [
              {
                type: "image",
                data: "ZmFrZS1pbWFnZQ==",
                mimeType: "image/png",
              },
            ],
            timestamp: 2,
            queueType: "followUp",
          },
        },
      });
      expect(session._followUpMessages).toEqual(["Queued first"]);
      expect(session.agent.followUpQueue.messages).toHaveLength(1);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("resets transcript key allocation after switching transcript baselines", async () => {
      const tmpDir = fs.mkdtempSync(
        path.join(os.tmpdir(), "pi-web-transcript-"),
      );
      const sessionManager = SessionManager.create(tmpDir, tmpDir);
      sessionManager.appendMessage({
        role: "user",
        content: [{ type: "text", text: "Selected transcript" }],
        timestamp: Date.now(),
      });
      const sessionFile = sessionManager.getSessionFile();
      if (!sessionFile) {
        throw new Error("session file was not created");
      }

      fs.writeFileSync(
        sessionFile,
        [
          JSON.stringify({
            type: "session",
            version: 3,
            id: sessionManager.getSessionId(),
            timestamp: new Date().toISOString(),
            cwd: tmpDir,
          }),
          ...sessionManager.getEntries().map(entry => JSON.stringify(entry)),
        ].join("\n"),
      );

      const subscribeSpy = vi.fn().mockReturnValue(() => {});
      createAgentSessionMock.mockResolvedValueOnce({
        session: {
          sessionFile,
          sessionId: sessionManager.getSessionId(),
          isStreaming: false,
          bindExtensions: vi.fn().mockResolvedValue(undefined),
          subscribe: subscribeSpy,
          prompt: vi.fn().mockResolvedValue(undefined),
          sessionManager,
        },
      });

      const handler = (context.events.subscribe as ReturnType<typeof vi.fn>)
        .mock.calls[0]?.[0] as
        | ((event: Record<string, unknown>) => void)
        | undefined;
      handler?.({
        type: "message_start",
        message: { role: "assistant", content: "Live before switch" },
      });

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "switch-selected",
              type: "switch_session",
              sessionPath: sessionFile,
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 10));

      (
        ws as unknown as { trigger: (event: string, data: Buffer) => void }
      ).trigger(
        "message",
        Buffer.from(
          JSON.stringify({
            type: "command",
            payload: {
              id: "prompt-selected",
              type: "prompt",
              message: "Activate selected transcript session",
            },
          }),
        ),
      );
      await new Promise(r => setTimeout(r, 20));

      const selectedSessionEventHandler = subscribeSpy.mock.calls[0]?.[0] as
        | ((event: object) => void)
        | undefined;
      selectedSessionEventHandler?.({
        type: "message_start",
        message: { role: "assistant", content: "Selected after switch" },
      });

      const sendCalls = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
        call => JSON.parse(call[0] as string),
      );
      const transcriptStarts = sendCalls.filter(
        call =>
          call.type === "event" && call.payload.type === "transcript_start",
      );
      const liveStart = transcriptStarts.find(
        call => call.payload.sessionPath !== sessionFile,
      );
      const selectedStart = transcriptStarts.find(
        call => call.payload.sessionPath === sessionFile,
      );

      expect(liveStart?.payload.message.transcriptKey).toBe("live:1");
      expect(liveStart?.payload.message.content).toEqual([]);
      expect(selectedStart?.payload.message.transcriptKey).toBe("live:1");
      expect(selectedStart?.payload.message.content).toEqual([]);
      expect(selectedStart?.payload.sessionPath).toBe(sessionFile);

      fs.rmSync(tmpDir, { recursive: true, force: true });
    });

    it("coalesces queued stats pushes to the latest pending session path", async () => {
      await new Promise(r => setTimeout(r, 10));
      (ws.send as ReturnType<typeof vi.fn>).mockClear();

      let resolveFirstStats: ((value: any) => void) | undefined;
      const firstStats = new Promise(resolve => {
        resolveFirstStats = resolve;
      });

      const buildStatsSpy = vi
        .spyOn(adapter as any, "buildSessionStats" as any)
        .mockReturnValueOnce(firstStats as never)
        .mockResolvedValueOnce({
          tokens: 42,
          contextWindow: 100,
          percent: 42,
          messageCount: 7,
          cost: 0,
          inputTokens: 1,
          outputTokens: 2,
          cacheReadTokens: 3,
          cacheWriteTokens: 4,
        } as never);

      (adapter as any).sessionStatsPusher.queue("session-a");
      await Promise.resolve();
      (adapter as any).sessionStatsPusher.queue("session-b");
      (adapter as any).sessionStatsPusher.queue("session-c");

      resolveFirstStats?.({
        tokens: 10,
        contextWindow: 100,
        percent: 10,
        messageCount: 1,
        cost: 0,
        inputTokens: 0,
        outputTokens: 0,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
      });
      await new Promise(r => setTimeout(r, 10));

      expect(buildStatsSpy).toHaveBeenNthCalledWith(1, "session-a");
      expect(buildStatsSpy).toHaveBeenNthCalledWith(2, "session-c");

      const statsEvents = (ws.send as ReturnType<typeof vi.fn>).mock.calls
        .map(call => JSON.parse(call[0] as string))
        .filter(
          call =>
            call.type === "event" && call.payload.type === "session_stats",
        );
      expect(statsEvents).toHaveLength(2);
      expect(statsEvents.map(call => call.payload.sessionPath)).toEqual([
        "session-a",
        "session-c",
      ]);
    });
  });
});
