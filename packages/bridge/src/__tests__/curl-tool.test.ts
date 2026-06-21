import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createCurlTool } from "../curl-tool.js";

vi.mock("node:child_process", async importOriginal => {
  const original = await importOriginal<typeof import("node:child_process")>();
  return { ...original, spawn: vi.fn() };
});

function childProcess() {
  const child = new EventEmitter() as EventEmitter & {
    stdout: PassThrough;
    stderr: PassThrough;
    kill: ReturnType<typeof vi.fn>;
  };
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.kill = vi.fn(() => true);
  vi.mocked(spawn).mockReturnValue(
    child as unknown as ChildProcessWithoutNullStreams,
  );
  return child;
}

function execute(
  args: string[],
  signal?: AbortSignal,
) {
  return createCurlTool("/workspace").execute(
    "curl-1",
    { args },
    signal,
    undefined,
    {} as never,
  );
}

describe("curl tool", () => {
  beforeEach(() => vi.mocked(spawn).mockReset());

  it("forwards arguments unchanged and in order without a shell", async () => {
    const child = childProcess();
    const args = ["-H", "x-name: a b", "https://example.com/?x=$(id)&y=*;"];
    const result = execute(args);

    expect(spawn).toHaveBeenCalledWith("curl", args, {
      cwd: "/workspace",
      env: process.env,
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
    });
    child.emit("close", 0);
    await result;
  });

  it("returns stdout unchanged as tool content", async () => {
    const child = childProcess();
    const result = execute(["https://example.com"]);

    child.stdout.write("first line\n");
    child.stdout.write("second line");
    child.emit("close", 0);

    await expect(result).resolves.toMatchObject({
      content: [{ type: "text", text: "first line\nsecond line" }],
    });
  });

  it("returns stderr to the agent for non-zero exits", async () => {
    const child = childProcess();
    const result = execute(["--fail", "https://example.com"]);

    child.stderr.write("curl: (22) requested URL returned error: 404\n");
    child.emit("close", 22);

    await expect(result).resolves.toMatchObject({
      content: [
        {
          type: "text",
          text: "curl: (22) requested URL returned error: 404\n",
        },
      ],
      details: {
        stderr: "curl: (22) requested URL returned error: 404\n",
        exitCode: 22,
      },
    });
  });

  it("rejects when curl cannot be started", async () => {
    const child = childProcess();
    const result = execute(["https://example.com"]);
    const error = new Error("spawn curl ENOENT");

    child.emit("error", error);

    await expect(result).rejects.toBe(error);
  });

  it("kills curl when an active call is aborted", async () => {
    const child = childProcess();
    const controller = new AbortController();
    const result = execute(["https://example.com"], controller.signal);

    controller.abort();
    expect(child.kill).toHaveBeenCalledOnce();
    child.emit("close", null);
    await result;
  });

  it("kills curl immediately when the signal is already aborted", async () => {
    const child = childProcess();
    const controller = new AbortController();
    controller.abort();

    const result = execute(["https://example.com"], controller.signal);

    expect(child.kill).toHaveBeenCalledOnce();
    child.emit("close", null);
    await result;
  });
});
