import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const {
  createAgentSessionFromServicesMock,
  createAgentSessionServicesMock,
  createBashToolDefinitionMock,
  createEditToolDefinitionMock,
  createReadToolDefinitionMock,
  createWriteToolDefinitionMock,
} = vi.hoisted(() => ({
  createAgentSessionFromServicesMock: vi.fn(),
  createAgentSessionServicesMock: vi.fn(),
  createBashToolDefinitionMock: vi.fn(),
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
    createBashToolDefinition: createBashToolDefinitionMock,
    createEditToolDefinition: createEditToolDefinitionMock,
    createReadToolDefinition: createReadToolDefinitionMock,
    createWriteToolDefinition: createWriteToolDefinitionMock,
  };
});

import {
  buildDetachedShellCommandPrefix,
  createDetachedAgentSession,
} from "../detached-session.js";
import { detectWorkspaceEnvironments } from "../workspace-environment.js";

describe("detached-session", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-web-detached-session-"));
    createAgentSessionFromServicesMock.mockReset();
    createAgentSessionServicesMock.mockReset();
    createBashToolDefinitionMock.mockReset();
    createEditToolDefinitionMock.mockReset();
    createReadToolDefinitionMock.mockReset();
    createWriteToolDefinitionMock.mockReset();
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("adds direnv and local venv activation ahead of the user prefix", () => {
    fs.writeFileSync(path.join(tmpDir, ".envrc"), "use nix\n", "utf8");
    fs.mkdirSync(path.join(tmpDir, ".venv", "bin"), { recursive: true });
    fs.writeFileSync(
      path.join(tmpDir, ".venv", "bin", "activate"),
      "# activate\n",
      "utf8",
    );

    const prefix = buildDetachedShellCommandPrefix(tmpDir, "echo base");

    expect(prefix).toContain(
      'eval "$(direnv export bash 2>/dev/null)" || true',
    );
    expect(prefix).toContain(". '.venv/bin/activate'");
    expect(prefix).toContain("echo base");
    expect(prefix?.indexOf("direnv export bash")).toBeLessThan(
      prefix?.indexOf("echo base") ?? 0,
    );
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

  it("returns only the user prefix when no workspace activation is needed", () => {
    expect(buildDetachedShellCommandPrefix(tmpDir, "echo base")).toBe(
      "echo base",
    );
  });

  it("builds env-aware custom tools for detached sessions", async () => {
    fs.writeFileSync(path.join(tmpDir, ".envrc"), "use nix\n", "utf8");
    fs.mkdirSync(path.join(tmpDir, "venv", "bin"), { recursive: true });
    fs.writeFileSync(
      path.join(tmpDir, "venv", "bin", "activate"),
      "# activate\n",
      "utf8",
    );

    const services = {
      settingsManager: {
        getShellCommandPrefix: vi.fn().mockReturnValue("echo base"),
        getImageAutoResize: vi.fn().mockReturnValue(false),
      },
    };
    const readToolDefinition = { name: "read" };
    const bashToolDefinition = { name: "bash" };
    const editToolDefinition = { name: "edit" };
    const writeToolDefinition = { name: "write" };
    const sessionResult = { session: { sessionId: "session-123" } };
    const sessionManager = { getCwd: vi.fn().mockReturnValue(tmpDir) };

    createAgentSessionServicesMock.mockResolvedValue(services);
    createReadToolDefinitionMock.mockReturnValue(readToolDefinition);
    createBashToolDefinitionMock.mockReturnValue(bashToolDefinition);
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
    expect(createBashToolDefinitionMock).toHaveBeenCalledWith(
      tmpDir,
      expect.objectContaining({
        commandPrefix: expect.stringContaining("direnv export bash"),
      }),
    );
    expect(createBashToolDefinitionMock).toHaveBeenCalledWith(
      tmpDir,
      expect.objectContaining({
        commandPrefix: expect.stringContaining("venv/bin/activate"),
      }),
    );
    expect(createBashToolDefinitionMock).toHaveBeenCalledWith(
      tmpDir,
      expect.objectContaining({
        commandPrefix: expect.stringContaining("echo base"),
      }),
    );
    expect(createEditToolDefinitionMock).toHaveBeenCalledWith(tmpDir);
    expect(createWriteToolDefinitionMock).toHaveBeenCalledWith(tmpDir);
    expect(createAgentSessionFromServicesMock).toHaveBeenCalledWith({
      services,
      sessionManager,
      customTools: [
        readToolDefinition,
        bashToolDefinition,
        editToolDefinition,
        writeToolDefinition,
      ],
    });
    expect(result).toBe(sessionResult);
  });
});
