import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const {
  createAgentSessionFromServicesMock,
  createAgentSessionServicesMock,
  createCurlToolMock,
  createEditToolDefinitionMock,
  createReadToolDefinitionMock,
  createWriteToolDefinitionMock,
} = vi.hoisted(() => ({
  createAgentSessionFromServicesMock: vi.fn(),
  createAgentSessionServicesMock: vi.fn(),
  createCurlToolMock: vi.fn(),
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
    createAgentSessionServices: createAgentSessionServicesMock,
    createEditToolDefinition: createEditToolDefinitionMock,
    createReadToolDefinition: createReadToolDefinitionMock,
    createWriteToolDefinition: createWriteToolDefinitionMock,
  };
});

vi.mock("../curl-tool.js", () => ({
  createCurlTool: createCurlToolMock,
}));

import { createDetachedAgentSession } from "../detached-session.js";
import { danoVersionTool } from "../dano-version-tool.js";
import { resolveDanoLlmTimeoutMs } from "../llm-resilience.js";
import { detectWorkspaceEnvironments } from "../workspace-environment.js";

describe("detached-session", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-detached-session-"));
    createAgentSessionFromServicesMock.mockReset();
    createAgentSessionServicesMock.mockReset();
    createCurlToolMock.mockReset();
    createEditToolDefinitionMock.mockReset();
    createReadToolDefinitionMock.mockReset();
    createWriteToolDefinitionMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("defaults the model response timeout to 300 seconds", () => {
    expect(resolveDanoLlmTimeoutMs({})).toBe(300_000);
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

  it("builds custom tools for detached sessions", async () => {
    vi.stubEnv("DANO_LLM_TIMEOUT_MS", "1234");
    const applyOverrides = vi.fn();
    const services = {
      settingsManager: {
        getImageAutoResize: vi.fn().mockReturnValue(false),
        applyOverrides,
      },
    };
    const readToolDefinition = { name: "read" };
    const curlToolDefinition = { name: "curl" };
    const editToolDefinition = { name: "edit" };
    const writeToolDefinition = { name: "write" };
    const configuredAskUserQuestionTool = { name: "configured-question" };
    let sessionEventHandler: ((event: any) => void) | undefined;
    const sessionResult = {
      session: {
        sessionId: "session-123",
        subscribe: vi.fn((handler: (event: any) => void) => {
          sessionEventHandler = handler;
          return vi.fn();
        }),
      },
    };
    const sessionManager = { getCwd: vi.fn().mockReturnValue(tmpDir) };

    createAgentSessionServicesMock.mockResolvedValue(services);
    createReadToolDefinitionMock.mockReturnValue(readToolDefinition);
    createCurlToolMock.mockReturnValue(curlToolDefinition);
    createEditToolDefinitionMock.mockReturnValue(editToolDefinition);
    createWriteToolDefinitionMock.mockReturnValue(writeToolDefinition);
    createAgentSessionFromServicesMock.mockResolvedValue(sessionResult);

    const result = await createDetachedAgentSession(
      tmpDir,
      sessionManager as never,
      { askUserQuestionTool: configuredAskUserQuestionTool as never },
    );

    expect(createAgentSessionServicesMock).toHaveBeenCalledWith({
      cwd: tmpDir,
      resourceLoaderOptions: {
        additionalExtensionPaths: [
          expect.stringContaining("pi-heimdall/extensions/heimdall.ts"),
        ],
      },
    });
    expect(applyOverrides).toHaveBeenCalledWith({
      retry: {
        enabled: true,
        maxRetries: 10,
        provider: {
          timeoutMs: 1234,
          maxRetries: 0,
        },
      },
    });
    expect(createReadToolDefinitionMock).toHaveBeenCalledWith(tmpDir, {
      autoResizeImages: false,
    });
    expect(createCurlToolMock).toHaveBeenCalledWith(tmpDir);
    expect(createEditToolDefinitionMock).toHaveBeenCalledWith(tmpDir);
    expect(createWriteToolDefinitionMock).toHaveBeenCalledWith(tmpDir);
    expect(createAgentSessionFromServicesMock).toHaveBeenCalledWith({
      services,
      sessionManager,
      noTools: "builtin",
      customTools: [
        readToolDefinition,
        curlToolDefinition,
        editToolDefinition,
        writeToolDefinition,
        danoVersionTool,
        configuredAskUserQuestionTool,
      ],
    });
    expect(result).toBe(sessionResult);

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
  });

  it("fails fast when DANO_LLM_TIMEOUT_MS is invalid", async () => {
    vi.stubEnv("DANO_LLM_TIMEOUT_MS", "not-a-timeout");
    const services = {
      settingsManager: {
        getImageAutoResize: vi.fn().mockReturnValue(false),
        applyOverrides: vi.fn(),
      },
    };
    createAgentSessionServicesMock.mockResolvedValue(services);
    createAgentSessionFromServicesMock.mockResolvedValue({
      session: { subscribe: vi.fn() },
    });

    await expect(
      createDetachedAgentSession(tmpDir, {} as never),
    ).rejects.toThrow(
      'Invalid DANO_LLM_TIMEOUT_MS: expected a positive integer, received "not-a-timeout"',
    );
  });
});
