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
import { askUserQuestionTool } from "../ask-user-question.js";
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

  it("builds custom tools for detached sessions", async () => {
    const services = {
      settingsManager: {
        getImageAutoResize: vi.fn().mockReturnValue(false),
      },
    };
    const readToolDefinition = { name: "read" };
    const curlToolDefinition = { name: "curl" };
    const editToolDefinition = { name: "edit" };
    const writeToolDefinition = { name: "write" };
    const sessionResult = { session: { sessionId: "session-123" } };
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
    );

    expect(createAgentSessionServicesMock).toHaveBeenCalledWith({
      cwd: tmpDir,
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
        askUserQuestionTool,
      ],
    });
    expect(result).toBe(sessionResult);
  });
});
