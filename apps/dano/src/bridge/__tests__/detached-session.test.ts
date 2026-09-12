import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const {
  createAgentSessionFromServicesMock,
  createAgentSessionRuntimeMock,
  createAgentSessionServicesMock,
  createEditToolDefinitionMock,
  createReadToolDefinitionMock,
  createWriteToolDefinitionMock,
} = vi.hoisted(() => ({
  createAgentSessionFromServicesMock: vi.fn(),
  createAgentSessionRuntimeMock: vi.fn(),
  createAgentSessionServicesMock: vi.fn(),
  createEditToolDefinitionMock: vi.fn(),
  createReadToolDefinitionMock: vi.fn(),
  createWriteToolDefinitionMock: vi.fn(),
}));

vi.mock("@earendil-works/pi-coding-agent", async () => {
  const actual = await vi.importActual<
    typeof import("@earendil-works/pi-coding-agent")
  >("@earendil-works/pi-coding-agent");

  return {
    ...actual,
    createAgentSessionFromServices: createAgentSessionFromServicesMock,
    createAgentSessionRuntime: createAgentSessionRuntimeMock,
    createAgentSessionServices: createAgentSessionServicesMock,
    createEditToolDefinition: createEditToolDefinitionMock,
    createReadToolDefinition: createReadToolDefinitionMock,
    createWriteToolDefinition: createWriteToolDefinitionMock,
  };
});

import { createDetachedAgentSessionRuntime } from "../detached-session.js";
import { danoVersionTool } from "../dano-version-tool.js";
import { detectWorkspaceEnvironments } from "../workspace-environment.js";

describe("detached-session", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-detached-session-"));
    createAgentSessionFromServicesMock.mockReset();
    createAgentSessionRuntimeMock.mockReset();
    createAgentSessionServicesMock.mockReset();
    createEditToolDefinitionMock.mockReset();
    createReadToolDefinitionMock.mockReset();
    createWriteToolDefinitionMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("detects the workspace environments exposed to the UI", () => {
    const workspaceDir = path.join(tmpDir, "sample-app");
    fs.mkdirSync(path.join(workspaceDir, ".venv", "bin"), {
      recursive: true,
    });
    fs.writeFileSync(path.join(workspaceDir, ".envrc"), "use nix\n", "utf8");
    fs.writeFileSync(
      path.join(workspaceDir, ".venv", "bin", "activate"),
      [
        "# activate",
        "VIRTUAL_ENV_PROMPT=.venv",
        "export VIRTUAL_ENV_PROMPT",
      ].join("\n") + "\n",
      "utf8",
    );

    expect(detectWorkspaceEnvironments(workspaceDir)).toEqual([
      {
        type: "direnv",
        label: "direnv",
        detail: ".envrc",
      },
      {
        type: "python-venv",
        label: "sample-app",
        detail: ".venv/bin/activate",
      },
    ]);
  });

  it("prefers an explicit python env prompt when available", () => {
    const workspaceDir = path.join(tmpDir, "service-api");
    fs.mkdirSync(path.join(workspaceDir, ".venv", "bin"), {
      recursive: true,
    });
    fs.writeFileSync(
      path.join(workspaceDir, ".venv", "pyvenv.cfg"),
      "prompt = 'api-dev'\n",
      "utf8",
    );
    fs.writeFileSync(
      path.join(workspaceDir, ".venv", "bin", "activate"),
      "# activate\n",
      "utf8",
    );

    expect(detectWorkspaceEnvironments(workspaceDir)).toEqual([
      {
        type: "python-venv",
        label: "api-dev",
        detail: ".venv/bin/activate",
      },
    ]);
  });

  it("builds detached sessions without an unsandboxed curl tool", async () => {
    vi.useFakeTimers();
    const applyOverrides = vi.fn();
    const services = {
      settingsManager: {
        getImageAutoResize: vi.fn().mockReturnValue(false),
        getRetryEnabled: vi.fn().mockReturnValue(true),
        applyOverrides,
      },
    };
    const readToolDefinition = { name: "read" };
    const editToolDefinition = { name: "edit" };
    const writeToolDefinition = { name: "write" };
    const configuredAskUserQuestionTool = { name: "configured-question" };
    const providerRequestTool = { name: "provider_request" };
    const releaseCredentialBinding = vi.fn();
    const credentialBroker = {
      createTool: vi.fn().mockReturnValue(providerRequestTool),
      observe: vi.fn().mockReturnValue(releaseCredentialBinding),
    };
    let sessionEventHandler: ((event: any) => void) | undefined;
    const sessionResult = {
      session: {
        sessionId: "session-123",
        abort: vi.fn().mockResolvedValue(undefined),
        subscribe: vi.fn((handler: (event: any) => void) => {
          sessionEventHandler = handler;
          return vi.fn();
        }),
      },
    };
    const sessionManager = { getCwd: vi.fn().mockReturnValue(tmpDir) };

    createAgentSessionServicesMock.mockResolvedValue(services);
    createReadToolDefinitionMock.mockReturnValue(readToolDefinition);
    createEditToolDefinitionMock.mockReturnValue(editToolDefinition);
    createWriteToolDefinitionMock.mockReturnValue(writeToolDefinition);
    createAgentSessionFromServicesMock.mockResolvedValue(sessionResult);
    createAgentSessionRuntimeMock.mockImplementation(
      async (factory: (options: object) => Promise<object>, options: object) => {
        const created = (await factory(options)) as { session: object };
        return {
          session: created.session,
          setBeforeSessionInvalidate: vi.fn(),
        };
      },
    );

    const result = await createDetachedAgentSessionRuntime(
      tmpDir,
      sessionManager as never,
      {
        askUserQuestionTool: configuredAskUserQuestionTool as never,
        credentialBroker: credentialBroker as never,
        credentialBrokerScope: "user-a",
      },
    );

    expect(createAgentSessionServicesMock).toHaveBeenCalledWith({
      cwd: tmpDir,
      agentDir: expect.any(String),
      resourceLoaderOptions: {
        additionalExtensionPaths: [
          expect.stringContaining("pi-heimdall/extensions/heimdall.ts"),
        ],
        extensionsOverride: expect.any(Function),
      },
    });
    expect(applyOverrides).not.toHaveBeenCalled();
    expect(createReadToolDefinitionMock).toHaveBeenCalledWith(tmpDir, {
      autoResizeImages: false,
    });
    expect(createEditToolDefinitionMock).toHaveBeenCalledWith(tmpDir);
    expect(createWriteToolDefinitionMock).toHaveBeenCalledWith(tmpDir);
    expect(createAgentSessionFromServicesMock).toHaveBeenCalledWith({
      services,
      sessionManager,
      sessionStartEvent: undefined,
      noTools: "builtin",
      customTools: [
        readToolDefinition,
        editToolDefinition,
        writeToolDefinition,
        danoVersionTool,
        configuredAskUserQuestionTool,
        providerRequestTool,
      ],
    });
    expect(
      createAgentSessionFromServicesMock.mock.calls[0]?.[0].customTools,
    ).not.toContainEqual(expect.objectContaining({ name: "curl" }));
    expect(credentialBroker.createTool).toHaveBeenCalledWith("user-a");
    expect(credentialBroker.observe).toHaveBeenCalledWith(
      "user-a",
      sessionResult.session,
    );
    expect(result.runtime.session).toBe(sessionResult.session);
    expect(result.disposeDanoLlmResilience).toEqual(expect.any(Function));

    const overrideCallCount = applyOverrides.mock.calls.length;
    sessionEventHandler?.({
      type: "tool_execution_start",
      toolCallId: "read-1",
      toolName: "read",
      args: { path: "README.md" },
    });
    expect(applyOverrides).toHaveBeenCalledTimes(overrideCallCount);

    sessionEventHandler?.({
      type: "message_update",
      message: {
        role: "assistant",
        content: [{ type: "text", text: "partial response" }],
      },
    });
    expect(applyOverrides).toHaveBeenLastCalledWith({
      retry: { enabled: false },
    });

    sessionEventHandler?.({
      type: "message_start",
      message: { role: "user", content: "next request" },
    });
    expect(applyOverrides).toHaveBeenLastCalledWith({
      retry: { enabled: true },
    });

    sessionEventHandler?.({
      type: "tool_execution_start",
      toolCallId: "write-1",
      toolName: "write",
      args: { path: "result.txt" },
    });
    expect(applyOverrides).toHaveBeenLastCalledWith({
      retry: { enabled: false },
    });

    result.disposeDanoLlmResilience();
    expect(releaseCredentialBinding).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(15 * 60_000);
    expect(sessionResult.session.abort).not.toHaveBeenCalled();
  });

});
