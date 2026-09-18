import { execFile } from "node:child_process";
import { mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { afterEach, expect, it, vi } from "vitest";
import { createWorkerOutputRedactor } from "../worker-output-redaction.js";

const roots: string[] = [];
afterEach(async () => { await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });

async function harness() {
  const workspace = await mkdtemp(join(tmpdir(), "dano-output-redaction-"));
  roots.push(workspace);
  // Real Shell/Python verifies the operation, not the Linux UID boundary.
  const worker = { workspace, assertIsolated: vi.fn(async () => {}),
    execute: vi.fn(async (_name: string, parameters: Record<string, unknown>, signal?: AbortSignal) => {
      try {
        await promisify(execFile)("bash", ["-c", parameters.command as string], { cwd: workspace, signal });
        return { exitCode: 0 };
      } catch { return { exitCode: 1 }; }
    }) };
  return { workspace, worker, redact: createWorkerOutputRedactor(worker) };
}

it("scrubs large private outputs including chunk boundaries and shell metacharacters", async () => {
  const h = await harness();
  const path = join(h.workspace, "output'$(touch INJECTED).txt");
  const secret = "synthetic'$(touch SECRET_INJECTED)";
  const content = "x".repeat(65530) + secret + "y".repeat(200000) + secret;
  await writeFile(path, content, { mode: 0o600 });
  await h.redact(path, secret);
  expect(await readFile(path, "utf8")).toBe(content.replaceAll(secret, "[redacted]"));
  expect(await readdir(h.workspace)).toEqual(["output'$(touch INJECTED).txt"]);
  expect(h.worker.assertIsolated).toHaveBeenCalledTimes(2);
});

it("rejects symlinks, directories and missing files without rewriting their targets", async () => {
  const h = await harness();
  const target = join(h.workspace, "target");
  const link = join(h.workspace, "link");
  await writeFile(target, "synthetic-secret");
  await symlink(target, link);
  for (const path of [link, h.workspace, join(h.workspace, "missing")]) {
    await expect(h.redact(path, "synthetic-secret")).rejects.toThrow("WORKER_OUTPUT_REDACTION_FAILED");
  }
  expect(await readFile(target, "utf8")).toBe("synthetic-secret");
  expect((await readdir(h.workspace)).sort()).toEqual(["link", "target"]);
});

it("does not execute after cancellation or failed isolation", async () => {
  const h = await harness();
  await expect(h.redact("unused", "secret", AbortSignal.abort())).rejects.toThrow();
  expect(h.worker.execute).not.toHaveBeenCalled();
  h.worker.assertIsolated.mockRejectedValueOnce(new Error("not isolated"));
  await expect(h.redact("unused", "secret")).rejects.toThrow("not isolated");
  expect(h.worker.execute).not.toHaveBeenCalled();
  const lifetime = new AbortController();
  h.worker.assertIsolated.mockImplementationOnce(async () => { lifetime.abort(); });
  await expect(h.redact("unused", "secret", lifetime.signal)).rejects.toThrow();
  expect(h.worker.execute).not.toHaveBeenCalled();
});
